# AI PET/CT Reading Framework

Automated [18F]FDG PET/CT processing for Hodgkin lymphoma therapy response assessment, supporting physician Deauville score (DS) reading. The result is **InsightMIP**: coronal and sagittal maximum intensity projections of the baseline and follow-up scans side by side, with lesion overlays, anatomical tags (lung, liver, bone), and SUVmax of the mediastinal blood pool, the liver and the three hottest lesions.

The pipeline runs as [Argo Workflows](https://argoproj.github.io/workflows/) on Kubernetes. [Orthanc](https://www.orthanc-server.com/) is the DICOM gateway to your PACS and [HAPI FHIR](https://hapifhir.io/) optionally stores the result as a FHIR resource. For a follow-up study and, optionally, its baseline study (accession numbers), it:

1. Retrieves both studies from the PACS into Orthanc.
2. For each study (in parallel): selects the PET and whole-body CT series, converts DICOM to NIfTI, resamples the CT to the PET grid, segments organs on CT with [TotalSegmentator](https://github.com/wasserth/TotalSegmentator) (liver and mediastinal blood pool references) and FDG-avid lesions on PET/CT with an nnU-Net model, and computes SUV, reference uptake and the 2D MIP.
3. Concatenates the baseline MIP (left) and the follow-up MIP (right) into InsightMIP.
4. Stores the lesion segmentation (DICOM SEG) and InsightMIP in the follow-up study in Orthanc, sends them to the PACS and, optionally, posts a FHIR `DiagnosticReport`.

Results are for research and reading support. Verify them before clinical use.

## Repository layout

```
config/pvc.yaml            shared working volume for the pipeline
config/artifact-repository.yaml  S3 artifact repository (InsightMIP download in the Argo UI)
deploy/orthanc.yaml        Orthanc (official image orthancteam/orthanc), includes the OHIF viewer
deploy/hapi-fhir.yaml      HAPI FHIR (official image hapiproject/hapi), optional
workflows/
  pipeline.yaml            the pipeline: order of the steps only (per study, then InsightMIP)
  orthanc.yaml             Orthanc REST calls: find, retrieve, download, create DICOM, send
  dicom-to-nifti.yaml      DICOM -> NIfTI
  resample-ct.yaml         CT -> PET grid
  ct-segmentation.yaml     TotalSegmentator (GPU)
  pet-segmentation.yaml    nnU-Net lesion segmentation (GPU)
  analysis.yaml            SUV, reference uptake, DICOM SEG, MIP per study, InsightMIP
  fhir.yaml                FHIR DiagnosticReport
  rerun-insightmip.yaml    re-creates InsightMIP from a finished run (analysis only, no segmentation)
scripts/get-insightmip.sh  downloads InsightMIP of a run from the shared volume
microservices/             source and Dockerfile of each own container image
  dicom-to-nifti/          DICOM -> NIfTI (CT in HU, PET in SUV, any vendor)
  resize-volume/           CT -> PET grid
  pet-segmentation/        nnU-Net lesion segmentation (model weights not included, see its Dockerfile)
  petct-analysis/          SUV, reference organs, DICOM SEG, MIP (InsightMIP style)
```

Every file in `workflows/` is its own Argo `WorkflowTemplate`, named `pipeline-<file name>` (the main one is just `pipeline`); `pipeline` calls the others with `templateRef`, and they can be reused in other workflows.

## Requirements

- Kubernetes with at least one NVIDIA GPU node (about 50 GiB RAM for the segmentation steps)
- [Argo Workflows](https://argo-workflows.readthedocs.io/) and the `argo` CLI
- A PACS that accepts C-FIND and C-GET (or C-MOVE) from Orthanc, and C-STORE (or STOW-RS) for the results

All container images are public:

| Step | Image | Source |
|---|---|---|
| DICOM gateway, viewer | `orthancteam/orthanc:26.9.0` | official |
| FHIR server (optional) | `hapiproject/hapi:v8.12.0-1` | official |
| Orthanc / FHIR calls | `curlimages/curl:8.16.0` | official |
| DICOM -> NIfTI | `negarshahamiri/dicom-to-nifti:v2` | [microservices/dicom-to-nifti](microservices/dicom-to-nifti) |
| Resampling | `negarshahamiri/resize-volume:v2` | [microservices/resize-volume](microservices/resize-volume) |
| CT organ segmentation | `wasserth/totalsegmentator:2.2.1` | official |
| PET lesion segmentation | `negarshahamiri/pet-segmentation:v1` | [microservices/pet-segmentation](microservices/pet-segmentation) |
| Analysis | `negarshahamiri/petct-analysis:v4` | [microservices/petct-analysis](microservices/petct-analysis) |

## Setup

Run everything in one namespace (`kubectl config set-context --current --namespace <NAMESPACE>`). The Argo service account in that namespace must be allowed to run workflows.

**1. Orthanc login.** Create the secret `orthanc-login`. `users` configures Orthanc, `username`/`password` are used by the pipeline:

```sh
kubectl create secret generic orthanc-login \
  --from-literal=username=pipeline \
  --from-literal=password='<PASSWORD>' \
  --from-literal=users='{"pipeline": "<PASSWORD>"}'
```

Optional key `dicomweb-servers` to send results with DICOMweb, e.g. `{"pacs-web": {"Url": "https://pacs.example.org/dicom-web/", "Username": "...", "Password": "..."}}`.

**2. Orthanc.** In [deploy/orthanc.yaml](deploy/orthanc.yaml) set `ORTHANC__DICOM_MODALITIES` to your PACS (AE title, host, port) and register Orthanc's AE title `PETCT_AI` in the PACS. Then:

```sh
kubectl apply -f deploy/orthanc.yaml
kubectl apply -f deploy/hapi-fhir.yaml     # optional
kubectl apply -f config/pvc.yaml           # needs a ReadWriteMany storage class
```

Check the PACS connection: `curl -u pipeline:<PASSWORD> -X POST http://<orthanc>:8042/modalities/pacs/echo`.

**3. GPU.** The segmentation steps request `nvidia.com/gpu: 1`. If your GPU nodes have taints or need a node selector, add them in `ct-segmentation.yaml` and `pet-segmentation.yaml`.

**4. Register the templates:**

```sh
argo template create workflows/*.yaml
```

`pipeline` stores InsightMIP as an Argo output artifact in the S3 artifact repository [config/artifact-repository.yaml](config/artifact-repository.yaml) (ConfigMap `petct-artifacts`): set its bucket, endpoint (host only, without `https://`) and credentials secret, then `kubectl apply -f config/artifact-repository.yaml`. Argo mounts the repository secret in every step; without a working repository, every step stays at `PodInitializing` (event `FailedMount ... secret ... not found`).

## Running

Argo UI: *Workflow Templates → pipeline → Submit*, enter the accession numbers of the follow-up and the baseline study. Or:

```sh
argo submit --from workflowtemplate/pipeline \
  -p accession-number=<FOLLOW-UP ACCESSION> \
  -p baseline-accession-number=<BASELINE ACCESSION> --watch
```

Without `baseline-accession-number`, InsightMIP shows the follow-up study only.

Useful parameters (all have defaults, see [pipeline.yaml](workflows/pipeline.yaml)):

| Parameter | Default | Meaning |
|---|---|---|
| `baseline-accession-number` | empty | baseline study, shown left in InsightMIP |
| `pet-series` / `ct-series` | `*iter*tof*` / `*wb*` | SeriesDescription patterns for the PET and CT series |
| `retrieve-from-pacs` | `true` | `false` if the PACS already sends studies to Orthanc |
| `send-to-pacs` | `true` | send SEG and InsightMIP to `send-target` |
| `send-protocol` | `dimse` | `dicomweb` to use `dicomweb-servers` |
| `fhir-url` | empty | e.g. `http://hapi-fhir:8080/fhir` to post a `DiagnosticReport` |
| `pvc` | `petct-data` | shared volume |

Intermediate files are written to `/data/runs/<workflow name>/followup/` and `.../baseline/` on the shared volume (`dicom/`, `nifti/`, `ct-segmentation/`), InsightMIP to `/data/runs/<workflow name>/insightmip.png`. InsightMIP is also saved as an Argo output artifact in the workflow's artifact repository, so it can be downloaded without access to Orthanc: Argo UI → the run → step `insightmip` → *Artifacts*. The results (series `InsightMIP` and the lesion SEG in the follow-up study) can be viewed in OHIF at `http://<orthanc>:8042/ohif/`.

To download InsightMIP of a run to your computer with kubectl only (no Argo UI or port-forward needed):

```sh
scripts/get-insightmip.sh <pipeline run name>      # e.g. scripts/get-insightmip.sh pipeline-mfsbm
```

To re-create only InsightMIP from a finished run (for example after updating the analysis image), without segmenting again:

```sh
argo submit --from workflowtemplate/pipeline-rerun-insightmip -p run=<pipeline run name> \
  -p accession-number=<FOLLOW-UP ACCESSION> -p baseline-accession-number=<BASELINE ACCESSION> --watch
```

The shared volume and Orthanc contain patient data. Restrict access to the namespace, and delete old runs from the volume when they are no longer needed.
