#!/bin/sh
# Usage: scripts/get-insightmip.sh <run> [namespace] [output file]
set -eu

RUN=${1:?usage: $0 <run> [namespace] [output file]}
NAMESPACE=${2:-$(kubectl config view --minify -o jsonpath='{..namespace}')}
NAMESPACE=${NAMESPACE:-default}
OUTPUT=${3:-./${RUN}_insightmip.png}
PVC=${PVC:-petct-data}
POD=get-insightmip-$$

cleanup() { kubectl delete pod "$POD" -n "$NAMESPACE" --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT

kubectl run "$POD" -n "$NAMESPACE" --restart=Never --image=busybox:1.36.1 --overrides="{
  \"spec\": {
    \"containers\": [{\"name\": \"$POD\", \"image\": \"busybox:1.36.1\", \"command\": [\"sleep\", \"300\"],
      \"volumeMounts\": [{\"name\": \"data\", \"mountPath\": \"/data\", \"readOnly\": true}]}],
    \"volumes\": [{\"name\": \"data\", \"persistentVolumeClaim\": {\"claimName\": \"$PVC\", \"readOnly\": true}}]
  }}" >/dev/null
kubectl wait -n "$NAMESPACE" --for=condition=Ready "pod/$POD" --timeout=120s >/dev/null

FILE=/data/runs/$RUN/insightmip.png
if ! kubectl exec -n "$NAMESPACE" "$POD" -- test -f "$FILE"; then
  echo "ERROR: $FILE not found. Runs on the volume:" >&2
  kubectl exec -n "$NAMESPACE" "$POD" -- ls /data/runs >&2
  exit 1
fi
kubectl exec -n "$NAMESPACE" "$POD" -- cat "$FILE" > "$OUTPUT"
echo "InsightMIP of $RUN -> $OUTPUT"
command -v open >/dev/null 2>&1 && open "$OUTPUT" || true
