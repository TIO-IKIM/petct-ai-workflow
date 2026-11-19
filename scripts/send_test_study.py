#!/usr/bin/env python3
"""Send a PET/CT study to Orthanc to test the whole pipeline.

The study is uploaded as a new study and its patient gets a diagnosis in FHIR, so the
listener, broker and Sensor start the pipeline for it.

    python3 scripts/send_test_study.py <dicom folder> [--icd10 C81.9] [-n <namespace>]
"""
import argparse
import base64
import http.client
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ORTHANC = "http://localhost:18042"
FHIR = "http://localhost:18080/fhir"


def kubectl(*args):
    return subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True).stdout


class Tunnel:
    """kubectl port-forward to a service; restarted when the connection drops."""

    def __init__(self, namespace, service, ports):
        self.command = ["kubectl", "port-forward", "-n", namespace, service, ports]
        self.service = service
        self.local_port = int(ports.split(":")[0])
        self.process = None
        self.start()

    def start(self, timeout=30):
        """Starts the port-forward and waits until the local port accepts connections."""
        self.stop()
        self.process = subprocess.Popen(self.command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                break
            try:
                socket.create_connection(("localhost", self.local_port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.5)
        sys.exit(f"ERROR: kubectl port-forward to {self.service} on port {self.local_port} did not come up")

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait()


def secret(namespace, key):
    value = kubectl("get", "secret", "orthanc-login", "-n", namespace, "-o", f"jsonpath={{.data.{key}}}")
    return base64.b64decode(value).decode()


def request(method, url, body=None, auth=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as response:
        text = response.read()
        return json.loads(text) if text else None


CONNECTION_ERRORS = (ConnectionError, TimeoutError, http.client.HTTPException, urllib.error.URLError)


class Orthanc:
    def __init__(self, username, password, tunnel):
        self.auth = (username, password)
        self.tunnel = tunnel

    def upload(self, file, attempts=5):
        """Uploads one file; reconnects and retries if the port-forward drops."""
        for attempt in range(1, attempts + 1):
            try:
                return request("POST", f"{ORTHANC}/instances", file.read_bytes(), self.auth, "application/dicom")
            except urllib.error.HTTPError:
                raise
            except CONNECTION_ERRORS as error:
                if attempt == attempts:
                    raise
                print(f"  connection lost ({error.__class__.__name__}), reconnecting ({attempt}/{attempts - 1})")
                self.tunnel.start()

    def study(self, study_id):
        return request("GET", f"{ORTHANC}/studies/{study_id}", auth=self.auth)

    def delete_study(self, study_id):
        request("DELETE", f"{ORTHANC}/studies/{study_id}", auth=self.auth)

    def studies_with_accession(self, accession):
        return request("POST", f"{ORTHANC}/tools/find",
                       {"Level": "Study", "Query": {"AccessionNumber": accession}}, self.auth)

    def set_accession(self, study_id, accession):
        """Copy of the study with this AccessionNumber (Orthanc gives it new UIDs); the original is deleted.

        Large studies take minutes to copy, so it runs as an Orthanc job and is polled, reconnecting if needed.
        """
        job = self.call("POST", f"/studies/{study_id}/modify",
                        {"Replace": {"AccessionNumber": accession}, "Force": True, "Asynchronous": True})["ID"]
        while True:
            state = self.call("GET", f"/jobs/{job}")
            if state["State"] == "Success":
                break
            if state["State"] in ("Failure", "Paused"):
                raise RuntimeError(f"Orthanc job {job} ({state['State']}): {state.get('ErrorDescription', '')}")
            print(f"  setting AccessionNumber: {state.get('Progress', 0)}%")
            time.sleep(5)
        self.call("DELETE", f"/studies/{study_id}")
        return state["Content"]["ID"]

    def call(self, method, path, body=None, attempts=5):
        """Orthanc request that reconnects and retries if the port-forward drops."""
        for attempt in range(1, attempts + 1):
            try:
                return request(method, f"{ORTHANC}{path}", body, self.auth)
            except urllib.error.HTTPError:
                raise
            except CONNECTION_ERRORS as error:
                if attempt == attempts:
                    raise
                print(f"  connection lost ({error.__class__.__name__}), reconnecting ({attempt}/{attempts - 1})")
                self.tunnel.start()


def retry(tunnel, function, *args, attempts=5):
    """Calls function(*args); reconnects the tunnel and retries if the connection drops."""
    for attempt in range(1, attempts + 1):
        try:
            return function(*args)
        except urllib.error.HTTPError:
            raise
        except CONNECTION_ERRORS as error:
            if attempt == attempts:
                raise
            print(f"  connection to {tunnel.service} lost ({error.__class__.__name__}), "
                  f"reconnecting ({attempt}/{attempts - 1})")
            tunnel.start()


def fhir_patient(patient_id):
    """FHIR id of the patient with this DICOM PatientID, created if missing."""
    found = request("GET", f"{FHIR}/Patient?" + urllib.parse.urlencode({"identifier": patient_id}))
    if found.get("entry"):
        return found["entry"][0]["resource"]["id"]
    created = request("POST", f"{FHIR}/Patient", {
        "resourceType": "Patient",
        "identifier": [{"system": "urn:dicom:patient-id", "value": patient_id}],
    }, content_type="application/fhir+json")
    return created["id"]


def add_diagnosis(fhir_id, icd10):
    found = request("GET", f"{FHIR}/Condition?" + urllib.parse.urlencode({"subject": f"Patient/{fhir_id}", "code": icd10}))
    if found.get("total", 0) > 0:
        return
    request("POST", f"{FHIR}/Condition", {
        "resourceType": "Condition",
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                                       "code": "active"}]},
        "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": icd10}]},
        "subject": {"reference": f"Patient/{fhir_id}"},
    }, content_type="application/fhir+json")


def is_dicom(file):
    """DICOM files have the marker 'DICM' after a 128-byte preamble."""
    with open(file, "rb") as f:
        return f.read(132)[128:] == b"DICM"


def dicom_files(folder):
    """All DICOM files in the folder, without reference segmentations (SEG folders)."""
    return sorted(f for f in folder.rglob("*")
                  if f.is_file() and "SEG" not in f.relative_to(folder).parts and is_dicom(f))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="folder with the DICOM files of one study (PET and CT)")
    parser.add_argument("--icd10", default="C81.9", help="diagnosis given to the patient in FHIR (default C81.9)")
    parser.add_argument("-n", "--namespace", default=None, help="Kubernetes namespace (default: current)")
    parser.add_argument("--accession", default=None,
                        help="AccessionNumber to give the study if its files have none (default: <PatientID>-<StudyDate>)")
    args = parser.parse_args()

    files = dicom_files(args.folder)
    if not files:
        sys.exit(f"No DICOM files in {args.folder}")
    namespace = args.namespace or kubectl("config", "view", "--minify", "-o", "jsonpath={..namespace}").strip()

    orthanc_tunnel = Tunnel(namespace, "svc/orthanc", "18042:8042")
    fhir_tunnel = Tunnel(namespace, "svc/hapi-fhir", "18080:8080")
    try:
        orthanc = Orthanc(secret(namespace, "username"), secret(namespace, "password"), orthanc_tunnel)

        # The first file tells us the study. If Orthanc already has it, delete it so it counts as new.
        answer = orthanc.upload(files[0])
        if answer["Status"] == "AlreadyStored":
            print("Study already in Orthanc, deleting it so it is sent as a new study")
            orthanc.delete_study(answer["ParentStudy"])
            answer = orthanc.upload(files[0])
        study = orthanc.study(answer["ParentStudy"])
        patient_id = study["PatientMainDicomTags"]["PatientID"]
        accession = study["MainDicomTags"].get("AccessionNumber", "")
        missing_accession = not accession
        if missing_accession:
            accession = args.accession or f"{patient_id}-{study['MainDicomTags'].get('StudyDate', '')}"
        print(f"Patient {patient_id}, accession {accession}" + (" (set by this script)" if missing_accession else ""))

        fhir_id = retry(fhir_tunnel, fhir_patient, patient_id)
        retry(fhir_tunnel, add_diagnosis, fhir_id, args.icd10)
        print(f"FHIR: Patient/{fhir_id} has diagnosis {args.icd10}")

        for number, file in enumerate(files[1:], start=2):
            orthanc.upload(file)
            if number % 100 == 0:
                print(f"  {number} / {len(files)} files")
        print(f"Sent {len(files)} files.")

        if missing_accession:
            # the listener needs an AccessionNumber; replace older copies that already have this one
            for old in orthanc.studies_with_accession(accession):
                print(f"Deleting older study {old} with accession {accession}")
                orthanc.delete_study(old)
            new_id = orthanc.set_accession(answer["ParentStudy"], accession)
            print(f"AccessionNumber {accession} set (Orthanc study {new_id})")
    except urllib.error.HTTPError as error:
        sys.exit(f"ERROR: {error.code} {error.reason} for {error.url}\n{error.read().decode(errors='replace')}")
    finally:
        orthanc_tunnel.stop()
        fhir_tunnel.stop()

    print(f"""
The listener picks the study up about 60 s after the last file. Follow it with:
  kubectl logs -n {namespace} deploy/petct-listener -f
  kubectl logs -n {namespace} deploy/petct-broker -f
  argo list -n {namespace} --since 10m""")


if __name__ == "__main__":
    main()
