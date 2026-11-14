#!/usr/bin/env bash
# Prove the Temporal worker deployment scales 1 -> 3 -> 1 from the task-queue backlog.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

kind delete cluster --name waterwatch >/dev/null 2>&1 || true
kind create cluster --name waterwatch --image kindest/node:v1.32.2 --wait 180s

helm repo add kedacore https://kedacore.github.io/charts
helm repo update kedacore
helm upgrade --install keda kedacore/keda \
  --namespace keda --create-namespace \
  --version 2.17.2 \
  --wait --timeout 180s

docker build -t waterwatch-worker:local -f k8s/Dockerfile.worker .
kind load docker-image waterwatch-worker:local --name waterwatch

kubectl apply -f k8s/temporal.yaml
kubectl wait --for=condition=available deployment/temporal --timeout=180s
kubectl apply -f k8s/worker.yaml
kubectl wait --for=condition=available deployment/temporal-worker --timeout=180s

kubectl port-forward svc/temporal 7233:7233 >/tmp/port-forward.log 2>&1 &
PF=$!
dump_failure() {
  kubectl get pods -A || true
  kubectl describe scaledobject temporal-worker || true
  kubectl get hpa -A || true
  kubectl logs deployment/temporal --tail=50 || true
  kubectl logs deployment/temporal-worker --tail=50 || true
}
cleanup() {
  code=$?
  if [ "$code" -ne 0 ]; then
    dump_failure
  fi
  kill "$PF" >/dev/null 2>&1 || true
  kind delete cluster --name waterwatch >/dev/null 2>&1 || true
  exit "$code"
}
trap cleanup EXIT

for _ in $(seq 1 30); do
  if python3 - <<'PY'
import socket
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", 7233))
except Exception:
    raise SystemExit(1)
finally:
    s.close()
PY
  then
    break
  fi
  sleep 1
done

PYTHONPATH=k8s python3 k8s/start_load.py

echo "waiting for scale-out"
kubectl wait --for=jsonpath='{.status.readyReplicas}'=3 deployment/temporal-worker --timeout=180s
echo "scaled to $(kubectl get deployment temporal-worker -o jsonpath='{.status.readyReplicas}')"

echo "waiting for scale-in"
kubectl wait --for=jsonpath='{.status.readyReplicas}'=1 deployment/temporal-worker --timeout=300s
echo "scaled back to $(kubectl get deployment temporal-worker -o jsonpath='{.status.readyReplicas}')"
echo KEDA_OK
