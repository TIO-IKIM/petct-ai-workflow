# Extending

## Other tumour types

Add one rule to [rules.json](../deploy/broker/rules.json) with its ICD-10 codes, a topic and a WorkflowTemplate, e.g. prostate cancer (C61) → `petct-workflow/prostate` → a PSMA workflow. With Argo Events, also add a Sensor for the new topic (see [sensor-lymphoma.yaml](../deploy/argo-events/sensor-lymphoma.yaml)).

## Connecting a hospital PACS (experimental, not tested)

Orthanc can fetch studies from a PACS and send results back; use the pipeline parameters `retrieve-from-pacs`, `send-to-pacs`, `send-target` and `send-protocol` (see [parameters](parameters-and-outputs.md)). For the Orthanc side, see the [Orthanc Book](https://orthanc.uclouvain.be/book/): [DICOM guide](https://orthanc.uclouvain.be/book/dicom-guide.html), [configuration](https://orthanc.uclouvain.be/book/users/configuration.html), [Docker images](https://orthanc.uclouvain.be/book/users/docker-orthancteam.html) and [DICOMweb](https://orthanc.uclouvain.be/book/plugins/dicomweb.html).
