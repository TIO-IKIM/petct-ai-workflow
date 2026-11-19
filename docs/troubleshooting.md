# Troubleshooting

| Symptom | Check |
|---|---|
| Nothing starts after upload | Listener log: was the study skipped (not newest, already processed, no AccessionNumber)? It waits 60 s after the last file. |
| Broker skips the study | Broker log lists the diagnoses found; only ICD-10 `C81*` has a rule. Is there a FHIR Patient with identifier = DICOM PatientID? |
| Study "already processed" | A workflow with this accession number did not fail. Delete it, then republish: `kubectl exec deploy/petct-listener -- python /app/listener.py publish <ACCESSION>`. |
| Each study runs twice | Both broker and Argo Events submit; set `TRIGGER` to `"false"` in [broker.yaml](../deploy/broker/broker.yaml). |
| Segmentation pod pending | No schedulable GPU node: add tolerations / node selector in [ct-segmentation.yaml](../workflows/ct-segmentation.yaml) and [pet-segmentation.yaml](../workflows/pet-segmentation.yaml). |
| A step is `OOMKilled` | Very thin CTs (1 mm, > 2000 slices) need more memory; raise the limit in that step's template. |
| Wrong series chosen | Read the `selectseries` log; force with `pet-series` / `ct-series`. |
| Port forward stopped | Restart it; it drops on pod restart or when idle. |
