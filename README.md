# Hydrosat tech challenge: Kubernetes Airflow bootstrap

This repository bootstraps a local Airflow environment on a local kind Kubernetes cluster.
It is intended for local DAG development and testing.

Key defaults:
- Kubernetes version: [1.35.1](https://hub.docker.com/layers/kindest/node/v1.35.1/images/sha256-b08d1f9b5244fb2aae7bea9861c1d372a9535f259efed62a9e92e6b5ab500529)
- Airflow Helm Chart version: [1.19.0](https://airflow.apache.org/docs/helm-chart/1.19.0/index.html)
- Airflow image tag from that chart: [3.1.7](https://airflow.apache.org/docs/apache-airflow/3.1.7/index.html)
- kind cluster name: `airflow-local`
- Kubernetes namespace: `airflow`

## Prerequisites

Install the following tools/components for this project:

| Product                                                                                  | Minimal version | Notes |
|------------------------------------------------------------------------------------------|-----------------|---|
| [Docker](https://docs.docker.com/desktop/#next-steps)                                    | 29.2.1          | Required to run kind/Kubernetes locally. |
| [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installing-from-release-binaries) | 0.31.0          | Local Kubernetes cluster runtime. |
| [kubectl](https://kubernetes.io/docs/tasks/tools/#kubectl)                               | 1.35.1          | Used by `make` targets and scripts. |
| [make](https://www.gnu.org/software/make/)                                               | 3.81 | Runs project automation targets (`make init`, `make dev`, etc.). |
| [Helm](https://helm.sh/docs/intro/install/#through-package-managers)                     | 4.1.1           | Required to install Airflow chart. |
| [git](https://git-scm.com/downloads)                                                     | 2.53.0          | Version control. |

## Quick start

1. Clone this repository and move to its root:

```bash
git clone git@github.com:Hydrosat/hydrosat-tech-challenge-k8s-airflow-bootstrap.git
cd hydrosat-tech-challenge-k8s-airflow-bootstrap
```

2. Create the kind cluster and install Airflow:

```bash
make init
```

3. Open the Airflow UI:

```bash
make dev

# Then open http://localhost:8080 in your browser.
# The default credentials are:
# - Username: admin
# - Password: admin
```

> `make dev` chooses the first free local port starting at `8080`, then port-forwards the Airflow UI service to that port.

4. Develop and test Airflow DAGs

Create and update DAGs from the `dags` folder. Changes are taken into account directly.
There is no need to rebuild the local stack.

5. Clean up resources:

```bash
make destroy
```

## DAG mounting model

This repository mounts local DAGs from `dags/` into the kind node, then into Airflow:
- `infra/kind/cluster.yaml` mounts `${PROJECT_ROOT}/dags` to `/mnt/airflow-dags` in the kind node.
- `infra/helm/airflow-dags-pv.yaml` and `infra/helm/airflow-dags-pvc.yaml` define the `airflow-dags` PV/PVC.
- `infra/values/airflow.yaml` configures Airflow to use that existing claim.

The sample DAG is `dags/hello_world.py`.

## Useful commands

```bash
make help              # list all targets
make status            # show Airflow pods and services
make reset             # reinstall Airflow in the existing cluster
make destroy           # uninstall Airflow and delete the kind cluster
make admin-token       # print token for local-admin service account
```
