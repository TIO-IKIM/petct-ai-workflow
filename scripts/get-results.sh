#!/bin/sh
# Copies the results of the latest successful pipeline run for an accession number to a local folder.
# Usage: scripts/get-results.sh <accession number> <output folder> [namespace]
#   e.g. scripts/get-results.sh AUTOPET-249C-FU data_autopet/results/PETCT_249c02c01c
set -eu

ACC=${1:?usage: $0 <accession number> <output folder> [namespace]}
OUT=${2:?usage: $0 <accession number> <output folder> [namespace]}
NAMESPACE=${3:-$(kubectl config view --minify -o jsonpath='{..namespace}')}
NAMESPACE=${NAMESPACE:-default}
PVC=${PVC:-petct-data}
POD=get-results-$$

# runs of the WorkflowTemplate "pipeline", whether submitted with argo or started by the Sensor
RUN=$(kubectl get workflows -n "$NAMESPACE" \
  -l workflows.argoproj.io/phase=Succeeded \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.workflowTemplateRef.name}{" "}{.spec.arguments.parameters[?(@.name=="accession-number")].value}{"\n"}{end}' \
  | awk -v acc="$ACC" '$2 == "pipeline" && $3 == acc {run = $1} END {print run}')
[ -n "$RUN" ] || { echo "ERROR: no successful pipeline run for $ACC" >&2; exit 1; }

cleanup() { kubectl delete pod "$POD" -n "$NAMESPACE" --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT

kubectl run "$POD" -n "$NAMESPACE" --restart=Never --image=busybox:1.36.1 --overrides="{
  \"spec\": {
    \"containers\": [{\"name\": \"$POD\", \"image\": \"busybox:1.36.1\", \"command\": [\"sleep\", \"600\"],
      \"volumeMounts\": [{\"name\": \"data\", \"mountPath\": \"/data\", \"readOnly\": true}]}],
    \"volumes\": [{\"name\": \"data\", \"persistentVolumeClaim\": {\"claimName\": \"$PVC\", \"readOnly\": true}}]
  }}" >/dev/null
kubectl wait -n "$NAMESPACE" --for=condition=Ready "pod/$POD" --timeout=120s >/dev/null

# only the results, not the DICOM/NIfTI inputs
mkdir -p "$OUT"
kubectl exec -n "$NAMESPACE" "$POD" -- sh -c "cd /data/runs/$RUN && tar cf - insightmip.png \
  \$(ls -d current/nifti/result.png current/nifti/tumor_seg.* prior/nifti/result.png prior/nifti/tumor_seg.* 2>/dev/null)" \
  | tar xf - -C "$OUT"
echo "run: $RUN, accession: $ACC" > "$OUT/run.txt"

echo "$RUN -> $OUT"
find "$OUT" -type f | sort
