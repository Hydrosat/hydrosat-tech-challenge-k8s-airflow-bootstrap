SHELL := bash
.DEFAULT_GOAL := help

KIND_CLUSTER_KUBERNETES_VERSION ?= 1.34.3
KIND_CONFIG ?= infra/kind/cluster.yaml
KIND_CLUSTER_NAME ?= airflow-local
AIRFLOW_NAMESPACE ?= airflow
AIRFLOW_RELEASE ?= airflow
AIRFLOW_HELM_VERSION ?= 1.19.0
AIRFLOW_VALUES ?= infra/values/airflow.yaml
AIRFLOW_HELM_TIMEOUT ?= 10m

.PHONY: help init reset dev kind-create kind-delete airflow-repo airflow-namespace airflow-dags-volume airflow-install airflow-uninstall airflow-clean status metrics-install admin-rbac admin-token destroy

help:
	@printf "Available targets:\n"
	@printf "  init               Create kind cluster and install Airflow\n"
	@printf "  reset              Reinstall Airflow in the existing cluster\n"
	@printf "  destroy            Delete kind cluster (including Airflow)\n"
	@printf "  dev                Port-forward Airflow UI to first free local port from 8080\n"
	@printf "  kind-create        Create the kind cluster\n"
	@printf "  kind-delete        Delete the kind cluster\n"
	@printf "  airflow-install    Install/upgrade Airflow via Helm\n"
	@printf "  airflow-uninstall  Uninstall Airflow\n"
	@printf "  metrics-install    Install metrics-server for cluster metrics\n"
	@printf "  admin-rbac         Create a local cluster-admin service account\n"
	@printf "  admin-token        Print a token for the local admin service account\n"
	@printf "  status             Show Airflow pods and services\n"

init: kind-create metrics-install airflow-namespace airflow-dags-volume airflow-install admin-rbac

reset: airflow-uninstall airflow-clean airflow-install

dev:
	@AIRFLOW_WEB_PORT="$$(infra/scripts/airflow_ports.sh)"; \
	if kubectl -n $(AIRFLOW_NAMESPACE) get svc $(AIRFLOW_RELEASE)-api-server >/dev/null 2>&1; then \
		SVC="$(AIRFLOW_RELEASE)-api-server"; \
	elif kubectl -n $(AIRFLOW_NAMESPACE) get svc $(AIRFLOW_RELEASE)-webserver >/dev/null 2>&1; then \
		SVC="$(AIRFLOW_RELEASE)-webserver"; \
	else \
		echo "No Airflow UI service found (expected $(AIRFLOW_RELEASE)-api-server or $(AIRFLOW_RELEASE)-webserver)."; \
		exit 1; \
	fi; \
	echo "Using Airflow UI service: $$SVC"; \
	echo "Using Airflow web port: $${AIRFLOW_WEB_PORT}"; \
	echo "Open http://localhost:$${AIRFLOW_WEB_PORT}/ with login=admin and password=admin with your web browser to access the Airflow UI."; \
	kubectl -n $(AIRFLOW_NAMESPACE) port-forward svc/$$SVC "$${AIRFLOW_WEB_PORT}":8080


kind-create:
	@if kind get clusters 2>/dev/null | grep -qx "$(KIND_CLUSTER_NAME)"; then \
		echo "kind cluster '$(KIND_CLUSTER_NAME)' already exists; skipping create."; \
	else \
		PROJECT_ROOT="$(PWD)"; \
		if command -v cygpath >/dev/null 2>&1; then \
			PROJECT_ROOT="$$(cygpath -m "$${PROJECT_ROOT}")"; \
		fi; \
		tmpfile="$$(mktemp)"; \
		PROJECT_ROOT_TOKEN='$${PROJECT_ROOT}'; \
		KIND_CLUSTER_NAME_TOKEN='$${KIND_CLUSTER_NAME}'; \
		while IFS= read -r line || [[ -n "$$line" ]]; do \
			line="$${line//$$PROJECT_ROOT_TOKEN/$${PROJECT_ROOT}}"; \
			line="$${line//$$KIND_CLUSTER_NAME_TOKEN/$(KIND_CLUSTER_NAME)}"; \
			printf '%s\n' "$$line"; \
		done < "$(KIND_CONFIG)" > "$${tmpfile}"; \
		kind create cluster --config "$${tmpfile}" --image "kindest/node:v${KIND_CLUSTER_KUBERNETES_VERSION}"; \
		rm -f "$${tmpfile}"; \
	fi

kind-delete:
	@kind delete cluster --name $(KIND_CLUSTER_NAME)

airflow-repo:
	@helm repo add apache-airflow https://airflow.apache.org
	@helm repo update

airflow-namespace:
	@kubectl apply -f infra/helm/namespace.yaml

airflow-dags-volume:
	@kubectl apply -f infra/helm/airflow-dags-pv.yaml
	@kubectl apply -f infra/helm/airflow-dags-pvc.yaml

airflow-install: airflow-repo
	@helm upgrade --install \
		$(AIRFLOW_RELEASE) \
		apache-airflow/airflow \
		--version $(AIRFLOW_HELM_VERSION) \
		--namespace $(AIRFLOW_NAMESPACE) \
		--wait \
		--timeout $(AIRFLOW_HELM_TIMEOUT) \
		--values $(AIRFLOW_VALUES)


metrics-install:
	@infra/scripts/metrics_server.sh

admin-rbac:
	@kubectl apply -f infra/helm/cluster-admin.yaml

admin-token:
	@kubectl -n kube-system create token local-admin

airflow-uninstall:
	@if helm -n $(AIRFLOW_NAMESPACE) status $(AIRFLOW_RELEASE) >/dev/null 2>&1; then \
		helm uninstall $(AIRFLOW_RELEASE) --namespace $(AIRFLOW_NAMESPACE); \
	else \
		echo "Helm release '$(AIRFLOW_RELEASE)' not found; skipping uninstall."; \
	fi; \
	if kubectl -n $(AIRFLOW_NAMESPACE) get pvc -l release=$(AIRFLOW_RELEASE) >/dev/null 2>&1; then \
		echo "Deleting PVCs labeled release=$(AIRFLOW_RELEASE) in namespace $(AIRFLOW_NAMESPACE)"; \
		kubectl delete pvc -l release=$(AIRFLOW_RELEASE) --namespace $(AIRFLOW_NAMESPACE) || \
			echo "Warning: PVC deletion returned non-zero exit code (PVCs may already be gone)."; \
	else \
		echo "No PVCs found with label release=$(AIRFLOW_RELEASE) in namespace $(AIRFLOW_NAMESPACE); skipping."; \
	fi

airflow-clean:
	@echo "Cleaning all remaining Airflow resources in namespace $(AIRFLOW_NAMESPACE)..."
	@for resource in jobs configmaps secrets serviceaccounts rolebindings roles; do \
		items=$$(kubectl -n $(AIRFLOW_NAMESPACE) get $$resource -o name 2>/dev/null | grep -v "configmap/kube-root-ca.crt" | grep -v "serviceaccount/default"); \
		if [ -n "$$items" ]; then \
			echo "Deleting leftover $$resource..."; \
			echo "$$items" | xargs kubectl -n $(AIRFLOW_NAMESPACE) delete --ignore-not-found 2>/dev/null || true; \
		fi; \
	done
	@echo "Deleting all remaining PVCs in namespace $(AIRFLOW_NAMESPACE) (except DAGs)..."
	@kubectl -n $(AIRFLOW_NAMESPACE) get pvc -o name 2>/dev/null \
		| grep -v "persistentvolumeclaim/airflow-dags" \
		| xargs -r kubectl -n $(AIRFLOW_NAMESPACE) delete --ignore-not-found 2>/dev/null || true
	@echo "Waiting for pods to terminate..."
	@kubectl -n $(AIRFLOW_NAMESPACE) wait --for=delete pod --all --timeout=60s 2>/dev/null || true
	@echo "Namespace $(AIRFLOW_NAMESPACE) is clean."

status:
	@kubectl -n $(AIRFLOW_NAMESPACE) get pods
	@kubectl -n $(AIRFLOW_NAMESPACE) get svc

destroy:
	@if kind get clusters 2>/dev/null | grep -qx "$(KIND_CLUSTER_NAME)"; then \
		kind delete cluster --name $(KIND_CLUSTER_NAME); \
	else \
		echo "kind cluster '$(KIND_CLUSTER_NAME)' not found; nothing to delete."; \
	fi
