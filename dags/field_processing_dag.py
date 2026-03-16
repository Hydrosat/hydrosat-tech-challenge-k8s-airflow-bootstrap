"""
DAG 2 – Field-Level Processing (dynamic tasks)
===============================================

Triggered by the ``sentinel2_raw`` Asset that DAG 1 publishes.

1. A lightweight ``@task`` reads the pipeline config and returns only the
   fields whose ``planting_date ≤ execution_date`` (i.e. *active* fields).
2. A ``KubernetesPodOperator.partial().expand()`` fans out one pod per
   active field.  Each pod clips the satellite raster to the field extent,
   computes NDVI, reads yesterday's cumulative output, accumulates, and
   writes the result back to MinIO.
"""
from __future__ import annotations

import os

import pendulum
import yaml
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import Asset, dag, task
from kubernetes.client import models as k8s

# ── Shared constants (same as DAG 1) ────────────────────────────────────────
PIPELINE_IMAGE = "hydrosat-pipeline-copilot:latest"
CONFIG_PATH = "/opt/airflow/dags/config/pipeline_config.yaml"
SATELLITE_DATA_ASSET = Asset("s3://hydrosat-pipeline-copilot/raw/sentinel2")

DAGS_VOLUME = k8s.V1Volume(
    name="airflow-dags",
    persistent_volume_claim=k8s.V1PersistentVolumeClaimVolumeSource(
        claim_name="airflow-dags",
    ),
)
DAGS_MOUNT = k8s.V1VolumeMount(
    name="airflow-dags",
    mount_path="/opt/airflow/dags",
    read_only=True,
)


# ── DAG definition ───────────────────────────────────────────────────────────
@dag(
    dag_id="field_processing",
    description="Dynamic per-field satellite processing (NDVI + accumulation)",
    schedule=(SATELLITE_DATA_ASSET,),
    start_date=pendulum.datetime(2024, 3, 15, tz="UTC"),
    catchup=False,
    default_args={"owner": "hydrosat"},
    tags=["satellite", "sentinel2", "processing", "ndvi", "dynamic"],
)
def field_processing() -> None:
    # ── Step 1: determine which fields are active today ──────────────────
    @task(task_id="get_active_fields")
    def get_active_fields(ds: str | None = None) -> list[list[str]]:
        """
        Return a list of CLI argument lists – one per active field.

        Only fields with ``planting_date <= ds`` are included, so dynamic
        task mapping produces exactly the right number of pods.
        """
        cfg_path = os.path.join(os.path.dirname(__file__), "config", "pipeline_config.yaml")
        with open(cfg_path) as fh:
            cfg = yaml.safe_load(fh)

        active: list[list[str]] = []
        for field in cfg["fields"]:
            if ds is not None and ds >= field["planting_date"]:
                active.append([
                    "--field-id", field["id"],
                    "--date", ds,
                    "--config", CONFIG_PATH,
                ])
        return active

    # ── Step 2: fan-out one KPO pod per active field ─────────────────────
    field_args = get_active_fields()

    KubernetesPodOperator.partial(
        task_id="process_field",
        name="process-field",
        namespace="airflow",
        image=PIPELINE_IMAGE,
        image_pull_policy="Never",
        cmds=["python", "/opt/airflow/dags/scripts/process_field.py"],
        volumes=[DAGS_VOLUME],
        volume_mounts=[DAGS_MOUNT],
        on_finish_action="keep_pod",
        get_logs=True,
        log_events_on_failure=True,
        startup_timeout_seconds=300,
    ).expand(arguments=field_args)


field_processing()

