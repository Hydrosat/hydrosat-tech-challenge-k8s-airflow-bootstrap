"""
DAG 1 – Sentinel-2 Satellite Data Download / Simulation
========================================================

Runs daily.  A single KubernetesPodOperator task generates (or downloads)
a two-band GeoTIFF (B04 Red + B08 NIR) covering the configured AOI and
uploads it to the MinIO bucket.

On completion the ``sentinel2_raw`` Asset is published, which triggers the
downstream ``field_processing`` DAG.
"""
from __future__ import annotations

import pendulum
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import Asset, dag
from kubernetes.client import models as k8s

# ── Shared constants ─────────────────────────────────────────────────────────
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
    dag_id="sentinel2_download",
    description="Download / simulate Sentinel-2 data for the AOI",
    schedule="@daily",
    start_date=pendulum.datetime(2024, 3, 15, tz="UTC"),
    catchup=True,
    default_args={"owner": "hydrosat"},
    tags=["satellite", "sentinel2", "download"],
)
def sentinel2_download() -> None:
    KubernetesPodOperator(
        task_id="download_sentinel2_data",
        name="download-sentinel2",
        namespace="airflow",
        image=PIPELINE_IMAGE,
        image_pull_policy="Never",          # image is loaded into kind
        cmds=["python", "/opt/airflow/dags/scripts/download_satellite.py"],
        arguments=["--date", "{{ ds }}", "--config", CONFIG_PATH],
        volumes=[DAGS_VOLUME],
        volume_mounts=[DAGS_MOUNT],
        on_finish_action="keep_pod",
        get_logs=True,
        log_events_on_failure=True,
        startup_timeout_seconds=300,
        outlets=[SATELLITE_DATA_ASSET],
    )


sentinel2_download()

