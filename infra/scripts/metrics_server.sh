#!/usr/bin/env bash
set -euo pipefail

METRICS_SERVER_VERSION="0.8.1"
MANIFEST_URL="https://github.com/kubernetes-sigs/metrics-server/releases/download/v${METRICS_SERVER_VERSION}/components.yaml"

kubectl apply -f "${MANIFEST_URL}"

args="$(kubectl -n kube-system get deploy metrics-server -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null || true)"
if [[ -z "${args}" ]]; then
  echo "metrics-server deployment not found; retry after a moment." >&2
  exit 1
fi

patch_items=()
if [[ "${args}" != *"--kubelet-insecure-tls"* ]]; then
  patch_items+=("{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--kubelet-insecure-tls\"}")
fi
if [[ "${args}" != *"--kubelet-preferred-address-types="* ]]; then
  patch_items+=("{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname\"}")
fi

if (( ${#patch_items[@]} > 0 )); then
  IFS=,
  patch="[${patch_items[*]}]"
  kubectl -n kube-system patch deployment metrics-server --type='json' -p="${patch}"
fi

kubectl -n kube-system rollout status deployment/metrics-server --timeout=2m
