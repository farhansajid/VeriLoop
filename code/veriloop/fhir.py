"""Map VeriLoop loop events to HL7 FHIR R4 resources.

This is a structural mapping used to show how a loop iteration can be
exchanged with an electronic health record: an observation becomes an
`Observation`, a clinical request a `ServiceRequest` fulfilled through a
`Task`, and every loop decision carries a `Provenance` resource whose
signature field holds the ledger hash.  It is not a validated FHIR
implementation.
"""
from __future__ import annotations

LOINC = {"HR": ("8867-4", "Heart rate", "/min"),
         "RR": ("9279-1", "Respiratory rate", "/min"),
         "SPO2": ("59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry", "%"),
         "SBP": ("8480-6", "Systolic blood pressure", "mm[Hg]"),
         "TEMP": ("8310-5", "Body temperature", "Cel")}


def observation(patient_id, channel, value, when_iso, quality_ok=True):
    code, display, unit = LOINC[channel]
    return {
        "resourceType": "Observation",
        "status": "final" if quality_ok else "entered-in-error",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                  "code": "vital-signs"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]},
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": when_iso,
        "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit},
    }


def service_request(patient_id, req_id, tier, when_iso, reason):
    return {
        "resourceType": "ServiceRequest",
        "id": f"vl-{req_id}",
        "status": "active",
        "intent": "order",
        "priority": {2: "urgent", 3: "stat"}.get(tier, "routine"),
        "code": {"text": {2: "Remote teleconsultation", 3: "Emergency escalation"}.get(tier, "Re-measurement")},
        "subject": {"reference": f"Patient/{patient_id}"},
        "authoredOn": when_iso,
        "reasonCode": [{"text": reason}],
    }


def task(req_id, status, outcome=None):
    t = {"resourceType": "Task", "status": status, "intent": "order",
         "basedOn": [{"reference": f"ServiceRequest/vl-{req_id}"}]}
    if outcome:
        t["output"] = [{"type": {"text": "verification"}, "valueString": outcome}]
    return t


def provenance(target_ref, when_iso, ledger_hash, agent="VeriLoop-edge"):
    return {
        "resourceType": "Provenance",
        "target": [{"reference": target_ref}],
        "recorded": when_iso,
        "agent": [{"who": {"display": agent}}],
        "signature": [{"type": [{"system": "urn:iso-astm:E1762-95:2013", "code": "1.2.840.10065.1.12.1.14"}],
                       "when": when_iso, "who": {"display": agent},
                       "sigFormat": "application/octet-stream", "data": ledger_hash}],
    }
