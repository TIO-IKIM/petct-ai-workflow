"""Broker: routes studies by FHIR ICD-10 diagnosis to workflow topics and submits the Argo workflows."""
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

import paho.mqtt.client as mqtt

FHIR = os.environ.get("FHIR_URL", "http://hapi-fhir:8080/fhir").rstrip("/")
MQTT_HOST = os.environ.get("MQTT_HOST", "emqx-listeners")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
RULES_FILE = os.environ.get("RULES_FILE", "/config/rules.json")
# false: only route, Argo Events starts the workflows
TRIGGER = os.environ.get("TRIGGER", "true").lower() == "true"
ICD10 = "http://hl7.org/fhir/sid/icd-10"

TOPIC_STUDY = "petct/new-study"
TOPIC_WORKFLOWS = "petct-workflow/#"
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


def kubernetes(method, path, body=None):
    return request(method, K8S + path, body, {"Authorization": f"Bearer {open(f'{SA}/token').read()}"},
                   ssl.create_default_context(cafile=f"{SA}/ca.crt"))


def namespace():
    return open(f"{SA}/namespace").read().strip()


def rules():
    return json.load(open(RULES_FILE))["rules"]


def label(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", value)[:63].strip("-_.") or "none"


def publish(client, topic, message):
    client.publish(topic, json.dumps(message), qos=1)
    log("MQTT ->", topic, json.dumps(message))



def diagnoses(patient_id):
    """ICD-10 codes of the patient from FHIR."""
    codes = []
    patients = request("GET", f"{FHIR}/Patient?" + urllib.parse.urlencode({"identifier": patient_id}))
    for entry in patients.get("entry", []):
        conditions = request("GET", f"{FHIR}/Condition?" + urllib.parse.urlencode(
            {"subject": f"Patient/{entry['resource']['id']}", "_count": 200}))
        for c in conditions.get("entry", []):
            for coding in c["resource"].get("code", {}).get("coding", []):
                if coding.get("system") == ICD10 and coding["code"] not in codes:
                    codes.append(coding["code"])
    return codes


def route(client, study):
    codes = diagnoses(study["patient-id"])
    matched = False
    for rule in rules():
        # "C81*" matches any code starting with C81
        hits = [c for c in codes for p in rule["icd10"]
                if c == p or (p.endswith("*") and c.startswith(p[:-1]))]
        if hits:
            publish(client, rule["topic"], {**study, "icd10": hits[0], "rule": rule["name"]})
            matched = True
    if not matched:
        publish(client, TOPIC_SKIPPED, {**study, "reason": f"no rule for the diagnoses {codes or 'none in FHIR'}"})



def trigger(client, topic, study):
    rule = next((r for r in rules() if r["topic"] == topic), None)
    if rule is None:
        return publish(client, TOPIC_SKIPPED, {**study, "reason": f"no rule for topic {topic}"})

    accession = study["accession-number"]
    selector = urllib.parse.quote(f"petct-accession={label(accession)}")
    existing = kubernetes("GET", f"/apis/argoproj.io/v1alpha1/namespaces/{namespace()}/workflows"
                                 f"?labelSelector={selector}")["items"]
    active = [w["metadata"]["name"] for w in existing
              if w.get("status", {}).get("phase") not in ("Failed", "Error")]
    if active:
        return publish(client, TOPIC_SKIPPED, {**study, "reason": f"already processed or running: {', '.join(active)}"})

    parameters = {**rule.get("parameters", {}), "accession-number": accession,
                  "prior-accession-number": study.get("prior-accession-number", "")}
    workflow = kubernetes("POST", f"/apis/argoproj.io/v1alpha1/namespaces/{namespace()}/workflows", {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Workflow",
        "metadata": {"generateName": f"{rule['workflowTemplate']}-",
                     "labels": {"petct-accession": label(accession), "petct-rule": label(rule["name"]),
                                "petct-started-by": "broker"}},
        "spec": {"workflowTemplateRef": {"name": rule["workflowTemplate"]},
                 "arguments": {"parameters": [{"name": k, "value": str(v)} for k, v in parameters.items()]}},
    })
    log(f"submitted {workflow['metadata']['name']} ({rule['workflowTemplate']}) for {accession}"
        f" prior={parameters['prior-accession-number'] or '-'}")



def on_connect(client, userdata, flags, reason, properties):
    topics = [TOPIC_STUDY, TOPIC_WORKFLOWS] if TRIGGER else [TOPIC_STUDY]
    client.subscribe([(t, 1) for t in topics])
    log(f"subscribed to {', '.join(topics)} on {MQTT_HOST}:{MQTT_PORT}"
        + ("" if TRIGGER else " (TRIGGER=false: workflows are started by Argo Events)"))


def on_message(client, userdata, message):
    log("MQTT <-", message.topic, message.payload.decode(errors="replace"))
    try:
        study = json.loads(message.payload)
        if message.topic == TOPIC_STUDY:
            route(client, study)
        else:
            trigger(client, message.topic, study)
    except Exception as e:
        log("ERROR", repr(e))
        client.publish(TOPIC_SKIPPED, json.dumps({"topic": message.topic, "reason": f"error: {e!r}",
                                                  "payload": message.payload.decode(errors="replace")}))


if __name__ == "__main__":
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="petct-broker")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever(retry_first_connection=True)
