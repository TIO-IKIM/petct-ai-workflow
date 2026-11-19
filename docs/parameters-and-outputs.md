# Parameters and outputs

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `accession-number` | required | current study |
| `prior-accession-number` | empty | prior study, shown left in InsightMIP; without it, InsightMIP shows the current study only |
| `pet-series` / `ct-series` | `auto` | `auto` or a SeriesDescription pattern, e.g. `*iter*tof*` |
| `retrieve-from-pacs` | `false` | fetch studies from a hospital PACS first |
| `send-to-pacs` | `false` | send SEG and InsightMIP to `send-target` |
| `send-protocol` | `dimse` | `dicomweb` to use `dicomweb-servers` |
| `fhir-url` | empty | e.g. `http://hapi-fhir:8080/fhir` to post a `DiagnosticReport` |
| `pvc` | `petct-data` | shared volume |

All defaults are in [workflows/pipeline.yaml](../workflows/pipeline.yaml).

## How series are selected (`auto`)

- **PET:** image series, attenuation-corrected, convertible to SUV (Bq/ml with injected dose and patient weight, SUV, or Philips counts with the Philips SUV factor).
- **CT:** axial image series, same frame of reference as the PET, soft-tissue kernel preferred.

The `selectseries` step logs every candidate and why it was taken or rejected. Check this log on the first run at a new site.

## Outputs

| Where | What |
|---|---|
| Orthanc, current study | InsightMIP series and lesion DICOM SEG (view in OHIF: `http://<orthanc>:8042/ohif/`) |
| `/data/runs/<run>/insightmip.png` | InsightMIP |
| `/data/runs/<run>/{current,prior}/` | intermediates: `dicom/`, `nifti/`, `ct-segmentation/` |
| FHIR (optional) | `DiagnosticReport` |

```bash
scripts/get-insightmip.sh <run name> [namespace]                # InsightMIP of one run
scripts/get-results.sh <ACCESSION> <OUTPUT FOLDER> [namespace]   # InsightMIP, per-scan MIPs and lesion segmentations of the latest successful run
```

Both copy from the shared volume with `kubectl` only, no port forwarding needed.
