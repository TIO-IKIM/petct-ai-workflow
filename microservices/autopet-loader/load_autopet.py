"""Loads an autoPET patient (TCIA FDG-PET-CT-Lesions) into Orthanc and its diagnosis into FHIR.

The two studies get the AccessionNumbers <PREFIX>-BL (prior) and <PREFIX>-FU (current).
Environment: PATIENT_ID, PREFIX, ORTHANC_URL, ORTHANC_USERNAME, ORTHANC_PASSWORD, FHIR_URL, ICD10 (empty = none).
"""
import base64, io, json, os, sys, urllib.parse, urllib.request, zipfile

NBIA = "https://services.cancerimagingarchive.net/nbia-api/services/v1"
ORTHANC = os.environ["ORTHANC_URL"].rstrip("/")
AUTH = "Basic " + base64.b64encode(
    f'{os.environ["ORTHANC_USERNAME"]}:{os.environ["ORTHANC_PASSWORD"]}'.encode()).decode()
patient, prefix = os.environ["PATIENT_ID"], os.environ["PREFIX"]

def get(url, timeout=600):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()

def orthanc(method, path, body=None, ctype="application/json"):
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
    req = urllib.request.Request(ORTHANC + path, data=body, method=method,
                                 headers={"Authorization": AUTH, "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
    return json.loads(data) if data else None

# diagnosis in FHIR first, so the listener finds it when the studies arrive
FHIR = os.environ["FHIR_URL"].rstrip("/")
ICD10 = "http://hl7.org/fhir/sid/icd-10"
NAMES = {"C81.9": "Hodgkin lymphoma, unspecified", "C83.3": "Diffuse large B-cell lymphoma",
         "C43.9": "Malignant melanoma of skin, unspecified",
         "C34.9": "Malignant neoplasm of bronchus or lung, unspecified"}

def fhir(method, path, body=None):
    req = urllib.request.Request(FHIR + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/fhir+json",
                                          "Accept": "application/fhir+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

code = os.environ["ICD10"].strip()
if code:
    found = fhir("GET", "/Patient?" + urllib.parse.urlencode({"identifier": patient}))
    if found.get("entry"):
        fhir_patient = found["entry"][0]["resource"]["id"]
    else:
        fhir_patient = fhir("POST", "/Patient", {
            "resourceType": "Patient",
            "identifier": [{"system": "urn:dicom:patient-id", "value": patient}]})["id"]
    conditions = fhir("GET", "/Condition?" + urllib.parse.urlencode(
        {"subject": f"Patient/{fhir_patient}", "code": f"{ICD10}|{code}"}))
    if not conditions.get("entry"):
        fhir("POST", "/Condition", {
            "resourceType": "Condition",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                                           "code": "active"}]},
            "code": {"coding": [{"system": ICD10, "code": code, "display": NAMES.get(code, code)}]},
            "subject": {"reference": f"Patient/{fhir_patient}"}})
    print(f"FHIR: Patient/{fhir_patient} ({patient}) has Condition ICD-10 {code}", flush=True)

series = json.loads(get(f"{NBIA}/getSeries?" + urllib.parse.urlencode(
    {"Collection": "FDG-PET-CT-Lesions", "PatientID": patient})))
series = [s for s in series if s["Modality"] in ("CT", "PT")]
dates = sorted({s["StudyDate"].split()[0] for s in series})
if len(dates) != 2:
    sys.exit(f"ERROR: {patient} has {len(dates)} studies with CT/PET on TCIA, expected 2")
accession = {dates[0]: f"{prefix}-BL", dates[1]: f"{prefix}-FU"}

for acc in accession.values():  # replace earlier loads
    for study in orthanc("POST", "/tools/find",
                         {"Level": "Study", "Query": {"AccessionNumber": acc}}):
        orthanc("DELETE", f"/studies/{study}")
        print(f"removed old study {acc} ({study})")

loaded = {}  # TCIA study date -> Orthanc study ID
for s in series:
    date = s["StudyDate"].split()[0]
    print(f"{date} {s['Modality']} {s.get('SeriesDescription')}: downloading", flush=True)
    z = zipfile.ZipFile(io.BytesIO(get(f"{NBIA}/getImage?SeriesInstanceUID={s['SeriesInstanceUID']}")))
    n = 0
    for name in z.namelist():
        if name.lower().endswith(".dcm"):
            answer = orthanc("POST", "/instances", z.read(name), "application/dicom")
            loaded[date] = answer["ParentStudy"]
            n += 1
    print(f"  {n} instances", flush=True)

for date, study in loaded.items():  # set AccessionNumber (new UIDs), drop the original
    new = orthanc("POST", f"/studies/{study}/modify",
                  {"Replace": {"AccessionNumber": accession[date]}, "Force": True})
    orthanc("DELETE", f"/studies/{study}")
    print(f"study {date} -> AccessionNumber {accession[date]} (Orthanc study {new['ID']})")

print(f"\nDone. With listener and Sensor running, the pipeline starts in about a minute"
      f" (kubectl logs deploy/petct-listener). Otherwise run the pipeline with accession-number={prefix}-FU prior-accession-number={prefix}-BL")
