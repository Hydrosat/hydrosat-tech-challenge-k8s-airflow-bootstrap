# Hydrosat Tech Challenge – Sentinel-2 Geospatial Pipeline

> **AI tools used:** GitHub Copilot (JetBrains), Claude (Anthropic) for
> architecture planning and implementation assistance.

This repository implements a simplified geospatial data pipeline running on
**Airflow 3.x** and **Kubernetes** (via kind).  It simulates daily Sentinel-2
satellite data acquisition, computes per-field NDVI, and accumulates results
across days — mimicking the kind of daily-dependent pipelines used at Hydrosat.

## Architecture overview

```
┌──────────────────────────────────────────────────────────┐
│                    kind cluster                          │
│                                                          │
│  ┌──────────┐  Asset   ┌───────────────────────────────┐ │
│  │  DAG 1   │ ───────► │          DAG 2                │ │
│  │ sentinel │          │    field_processing           │ │
│  │ download │          │                               │ │
│  └────┬─────┘          │  @task get_active_fields()    │ │
│       │                │          │                    │ │
│       ▼                │    ┌─────┼─────┐              │ │
│  ┌─────────┐           │    ▼     ▼     ▼              │ │
│  │  KPO    │           │  KPO   KPO   KPO             │ │
│  │  pod    │           │ field1 field2 field3          │ │
│  └────┬────┘           └───┬─────┬─────┬──────────────┘ │
│       │                    │     │     │                 │
│       ▼                    ▼     ▼     ▼                 │
│  ┌─────────────────────────────────────────────┐         │
│  │                  MinIO (S3)                 │         │
│  │  raw/{date}/sentinel2_bands.tif             │         │
│  │  processed/{field_id}/{date}/result.json    │         │
│  └─────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────┘
```

### Key components

| Component | Description                                                                                                                                                                                                                                |
|---|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **DAG 1 – `sentinel2_download`** | Daily. Simulates Sentinel-2 B04 (Red) + B08 (NIR) raster for the AOI. Uploads GeoTIFF to MinIO. Publishes an Asset.                                                                                                                        |
| **DAG 2 – `field_processing`** | Triggered by DAG 1's Asset. Dynamically maps one KubernetesPodOperator per active field (planting_date ≤ execution_date). Each pod clips raster, computes NDVI, reads yesterday's cumulative output, accumulates, and writes JSON to MinIO. |
| **MinIO** | S3-compatible object storage running in-cluster.                                                                                                                                                                                           |
| **Custom Docker image** | `hydrosat-pipeline-copilot:latest` — Python 3.12 + rasterio, shapely, numpy, boto3.                                                                                                                                                        |
| **Pipeline config** | `dags/config/pipeline_config.yaml` — AOI, fields (with planting dates), S3 settings.                                                                                                                                                       |

### Design decisions

- **LocalExecutor** — avoids the double-pod overhead of KubernetesExecutor + KubernetesPodOperator.
  Light orchestration tasks run in the scheduler; heavy processing runs in dedicated KPO pods.
- **Asset-triggered chaining** — DAG 2 is automatically triggered when DAG 1 publishes
  the `sentinel2_raw` asset, replacing fragile time-based dependencies.
- **Dynamic task mapping** — `KubernetesPodOperator.partial().expand()` creates exactly
  one pod per active field, adapting to configuration changes without DAG edits.
- **Idempotent tasks** — both download and processing scripts check for existing outputs
  before writing, making re-runs safe.
- **DAGs PVC mount in KPO pods** — scripts and config live in `dags/` and are mounted
  into KPO pods via the same PVC, enabling instant iteration without image rebuilds.

## Key defaults

- Kubernetes version: [1.34.3](https://hub.docker.com/layers/kindest/node/v1.34.3/)
- Airflow Helm Chart version: [1.19.0](https://airflow.apache.org/docs/helm-chart/1.19.0/index.html)
- Airflow image tag: 3.x (from chart defaults)
- kind cluster name: `airflow-local`
- Kubernetes namespace: `airflow`

## Prerequisites

| Tool | Min version | Notes |
|---|---|---|
| [Docker](https://docs.docker.com/desktop/) | 29.2.1 | Required for kind and image building |
| [kind](https://kind.sigs.k8s.io/docs/user/quick-start/) | 0.31.0 | Local Kubernetes cluster |
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | 1.34.0 | Cluster management |
| [Helm](https://helm.sh/docs/intro/install/) | 4.1.1 | Airflow chart installation |
| [make](https://www.gnu.org/software/make/) | 3.81 | Build automation |
| [git](https://git-scm.com/) | 2.53.0 | Version control |

## Quick start

```bash
# 1. Clone and enter the repository
git clone <repo-url>
cd <repo-dir>

# 2. Bootstrap everything (kind + MinIO + Docker image + Airflow)
make init

# 3. Open the Airflow UI
make dev
# → http://localhost:8080  (admin / admin)
```

`make init` will:
1. Create the kind cluster
2. Install the metrics server
3. Create the airflow namespace and DAGs PVC
4. **Build** the `hydrosat-pipeline-copilot` Docker image
5. **Load** the image into the kind cluster
6. **Deploy MinIO** and create the `hydrosat-pipeline-copilot` bucket
7. **Apply RBAC** for KubernetesPodOperator
8. Install Airflow via Helm

## Running the pipeline

1. Open the Airflow UI (`make dev`)
2. Enable the `sentinel2_download` DAG — it will trigger on the next daily schedule
   or you can trigger it manually
3. Once DAG 1 completes, it publishes the `sentinel2_raw` Asset which automatically
   triggers the `field_processing` DAG
4. DAG 2 dynamically creates one pod per active field (fields whose planting date
   has passed), computes NDVI, and writes results to MinIO

### Inspecting results

```bash
# Port-forward MinIO console
make minio-port-forward
# → Open http://localhost:9001 (minioadmin / minioadmin)
# Browse the 'hydrosat-pipeline' bucket
```

## Pipeline configuration

Edit `dags/config/pipeline_config.yaml` to change:

- **AOI** — the bounding box for satellite data acquisition
- **Fields** — polygons with `id`, `name`, `crop`, `planting_date`, `geometry`
- **S3 settings** — endpoint, credentials, bucket name
- **Simulation settings** — raster resolution, CRS, max pixel dimensions

Changes are picked up immediately (no rebuild needed).

## DAG mounting model

This repository mounts local DAGs from `dags/` into the kind node, then into Airflow:
- `infra/kind/cluster.yaml` mounts `${PROJECT_ROOT}/dags` to `/mnt/airflow-dags` in the kind node.
- `infra/helm/airflow-dags-pv.yaml` and `infra/helm/airflow-dags-pvc.yaml` define the `airflow-dags` PV/PVC.
- `infra/values/airflow.yaml` configures Airflow to use that existing claim.
- KubernetesPodOperator pods also mount the same `airflow-dags` PVC to access scripts and config.

## Project structure

```
├── dags/
│   ├── config/
│   │   └── pipeline_config.yaml      # AOI, fields, S3, simulation settings
│   ├── scripts/
│   │   ├── download_satellite.py     # Runs in KPO pod: simulate & upload raster
│   │   └── process_field.py          # Runs in KPO pod: NDVI + accumulation
│   ├── sentinel2_download_dag.py     # DAG 1: satellite data download
│   ├── field_processing_dag.py       # DAG 2: dynamic field processing
│   └── hello_world.py               # Original example DAG
├── docker/
│   ├── Dockerfile                    # Custom image with geospatial libs
│   └── requirements.txt
├── infra/
│   ├── helm/
│   │   ├── airflow-dags-pv.yaml
│   │   ├── airflow-dags-pvc.yaml
│   │   ├── cluster-admin.yaml
│   │   ├── kpo-rbac.yaml            # RBAC for KubernetesPodOperator
│   │   ├── minio.yaml               # MinIO deployment + bucket init
│   │   └── namespace.yaml
│   ├── kind/
│   │   └── cluster.yaml
│   ├── scripts/
│   │   ├── airflow_ports.sh
│   │   └── metrics_server.sh
│   └── values/
│       └── airflow.yaml              # Helm values (LocalExecutor)
├── Makefile
└── README.md
```

## Data flow

```
Day N:
  DAG 1 → simulate Sentinel-2 → s3://hydrosat-pipeline-copilot/raw/2024-04-15/sentinel2_bands.tif
                                                           ↓ Asset trigger
  DAG 2 → get_active_fields() → [field_1, field_2]  (field_3 not yet planted)
           ↓                     ↓
           KPO pod (field_1)     KPO pod (field_2)
           ├── read raster       ├── read raster
           ├── clip to field     ├── clip to field
           ├── compute NDVI      ├── compute NDVI
           ├── read Day N-1      ├── read Day N-1     ← yesterday's output
           ├── accumulate        ├── accumulate
           └── write result.json └── write result.json
```

Output example (`processed/field_1/2024-04-15/result.json`):
```json
{
  "field_id": "field_1",
  "field_name": "North-West Corn Field",
  "crop": "corn",
  "date": "2024-04-15",
  "planting_date": "2024-03-15",
  "days_since_planting": 31,
  "mean_ndvi": 0.421,
  "cumulative_ndvi": 12.53,
  "cumulative_days_processed": 31,
  "average_ndvi_over_season": 0.404
}
```

## Useful commands

```bash
make help              # List all targets
make status            # Show Airflow pods and services
make reset             # Reinstall Airflow in the existing cluster
make destroy           # Delete kind cluster and all resources
make docker-build      # Rebuild the pipeline Docker image
make docker-load       # Reload image into kind
make minio-port-forward # Access MinIO console at localhost:9001
make admin-token       # Print token for local-admin service account
```
