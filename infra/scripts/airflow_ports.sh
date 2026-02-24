#!/usr/bin/env bash
set -euo pipefail

BASE_PORT="${AIRFLOW_WEB_BASE_PORT:-8080}"
MAX_PORT=$((BASE_PORT + 20))
current_port="${BASE_PORT}"

is_port_listening() {
  local port="$1"

  if command -v ss >/dev/null 2>&1; then
    ss -ltnH 2>/dev/null | awk -v p=":${port}\$" '$4 ~ "(^|[^0-9])" p {found=1; exit} END{exit found ? 0 : 1}'
    return $?
  fi

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi

  if command -v netstat >/dev/null 2>&1; then
    netstat -an 2>/dev/null | grep -Eiq "([:.])${port}[[:space:]].*LISTEN(ING)?"
    return $?
  fi

  if command -v powershell >/dev/null 2>&1; then
    powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >/dev/null 2>&1
    return $?
  fi

  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >/dev/null 2>&1
    return $?
  fi

  echo "Unable to check local TCP ports: ss, lsof, netstat, and PowerShell are unavailable." >&2
  exit 1
}

is_port_free() {
  local port="$1"
  ! is_port_listening "${port}"
}

while ! is_port_free "${current_port}"; do
  current_port=$((current_port + 1))
  if (( current_port > MAX_PORT )); then
    echo "No free port found in range ${BASE_PORT}-${MAX_PORT}." >&2
    exit 1
  fi
done

echo "${current_port}"
