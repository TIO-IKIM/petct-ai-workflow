"""DICOM listener: publishes complete Orthanc studies on MQTT petct/new-study."""
import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import paho.mqtt.client as mqtt

ORTHANC = os.environ.get("ORTHANC_URL", "http://orthanc:8042").rstrip("/")
ORTHANC_AUTH = "Basic " + base64.b64encode(
    f'{os.environ["ORTHANC_USERNAME"]}:{os.environ["ORTHANC_PASSWORD"]}'.encode()).decode()
FHIR = os.environ.get("FHIR_URL", "http://hapi-fhir:8080/fhir").rstrip("/")
MQTT_HOST = os.environ.get("MQTT_HOST", "emqx-listeners")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
ICD10 = "http://hl7.org/fhir/sid/icd-10"

TOPIC_STUDY = "petct/new-study"
TOPIC_SKIPPED = "petct/skipped"

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
K8S = "https://kubernetes.default.svc"


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def request(method, url, body=None, headers=None, ssl_context=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60, context=ssl_context) as r:
        text = r.read()
    return json.loads(text) if text else None


def orthanc(method, path, body=None):
    return request(method, ORTHANC + path, body, {"Authorization": ORTHANC_AUTH})


def fhir(path):
    return request("GET", FHIR + path, headers={"Accept": "application/fhir+json"})


def label(value):
    """Kubernetes label value (the Sensor puts it on the workflow as petct-accession)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", value)[:63].strip("-_.") or "none"


def diagnoses(patient_id):
    """ICD-10 codes of the patient from FHIR."""
    codes = []
    bundle = fhir("/Patient?" + urllib.parse.urlencode({"identifier": patient_id}))
    for entry in bundle.get("entry", []):
        conditions = fhir("/Condition?" + urllib.parse.urlencode(
            {"subject": f"Patient/{entry['resource']['id']}", "_count": 200}))
        for c in conditions.get("entry", []):
            for coding in c["resource"].get("code", {}).get("coding", []):
                if coding.get("system") == ICD10 and coding["code"] not in codes:
                    codes.append(coding["code"])
    return codes


def processed(accession):
    """Workflows already running or done for this accession number."""
    namespace = open(f"{SA}/namespace").read().strip()
    token = open(f"{SA}/token").read()
    selector = urllib.parse.quote(f"petct-accession={label(accession)}")
    items = request("GET", f"{K8S}/apis/argoproj.io/v1alpha1/namespaces/{namespace}/workflows"
                           f"?labelSelector={selector}", headers={"Authorization": f"Bearer {token}"},
                    ssl_context=ssl.create_default_context(cafile=f"{SA}/ca.crt"))["items"]
    return [w["metadata"]["name"] for w in items if w.get("status", {}).get("phase") not in ("Failed", "Error")]


def study_event(study_id):
    """Returns (event, None), or (event, reason) if skipped."""
    study = orthanc("GET", f"/studies/{study_id}")
    tags = study["MainDicomTags"]
    event = {"accession-number": tags.get("AccessionNumber", ""),
             "patient-id": study["PatientMainDicomTags"].get("PatientID", ""),
             "study-date": tags.get("StudyDate", ""),
             "orthanc-study-id": study_id}
    if not event["accession-number"]:
        return event, "no AccessionNumber"

    found = orthanc("POST", "/tools/find", {"Level": "Study", "Expand": True,
                                            "Query": {"PatientID": event["patient-id"]}})
    studies = sorted((s["MainDicomTags"].get("StudyDate", ""), s["MainDicomTags"].get("AccessionNumber", ""))
                     for s in found if s["MainDicomTags"].get("AccessionNumber"))
    later = [a for d, a in studies if d > event["study-date"]]
    if later:
        return event, f"not the newest study of the patient (later: {', '.join(later)})"
    earlier = [a for d, a in studies if d < event["study-date"]]

    running = processed(event["accession-number"])
    if running:
        return event, f"already processed or running: {', '.join(running)}"

    event.update({"icd10": ",".join(diagnoses(event["patient-id"])),
                  "prior-accession-number": earlier[-1] if earlier else "",
                  "label": label(event["accession-number"])})
    return event, None


def mqtt_client():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"petct-listener-{os.getpid()}")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


def announce(client, study_id):
    event, reason = study_event(study_id)
    topic, message = (TOPIC_STUDY, event) if reason is None else (TOPIC_SKIPPED, {**event, "reason": reason})
    client.publish(topic, json.dumps(message), qos=1).wait_for_publish(timeout=10)
    log("MQTT", topic, json.dumps(message))


def listen():
    client = mqtt_client()
    since = orthanc("GET", "/changes?last")["Last"]  # only new studies
    log(f"watching Orthanc {ORTHANC} for complete studies, publishing to {MQTT_HOST}:{MQTT_PORT}")
    while True:
        try:
            changes = orthanc("GET", f"/changes?since={since}&limit=100")
        except (urllib.error.URLError, OSError) as e:
            log("Orthanc not reachable:", e)
            time.sleep(10)
            continue
        for change in changes["Changes"]:
            if change["ChangeType"] != "StableStudy":
                continue
            try:
                announce(client, change["ID"])
            except urllib.error.HTTPError as e:
                if e.code != 404:  # study was deleted meanwhile
                    log(f"ERROR study {change['ID']}:", repr(e))
            except Exception as e:
                log(f"ERROR study {change['ID']}:", repr(e))
        since = changes["Last"]
        if changes["Done"]:
            time.sleep(5)


if __name__ == "__main__":
    if sys.argv[1:2] == ["publish"]:
        found = orthanc("POST", "/tools/find", {"Level": "Study", "Query": {"AccessionNumber": sys.argv[2]}})
        if not found:
            sys.exit(f"Study {sys.argv[2]} is not in Orthanc")
        announce(mqtt_client(), found[0])
    else:
        listen()
