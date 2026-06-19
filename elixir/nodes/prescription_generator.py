"""Node 5 — Prescription Generator. Formats doctor-entered prescription for patient delivery.
Uses Featherless AI. Locale-aware patient instructions.
"""
import json
import env_loader  # noqa: loads elixir/.env
from llm_client import call_llm, repair_json, llm_config_for

PRESCRIPTION_SYSTEM_PROMPT = """You are a medical scribe generating a prescription document and patient-friendly medication instructions.

You will receive:
- Clinic information (name, doctor name, qualifications)
- Patient information (name, DOB, contact)
- Diagnosis and clinical notes from the doctor
- Verified medications with dosage, frequency, duration, and instructions
- Follow-up date and instructions
- Target locale (BCP-47 code)

Generate two outputs:
1. A FORMAL PRESCRIPTION — structured for printing or official records
2. PATIENT INSTRUCTIONS — plain-language instructions the patient can understand and act on

Output ONLY valid JSON:
{
  "formal_prescription": {
    "clinic_name": "<clinic name>",
    "doctor_name": "<doctor name>",
    "doctor_qualifications": "<qualifications>",
    "date": "<today's date>",
    "patient_name": "<patient name>",
    "patient_dob": "<dob>",
    "diagnosis": "<diagnosis>",
    "medications": [
      {
        "name": "<drug name>",
        "dosage": "<dosage>",
        "frequency": "<frequency>",
        "duration": "<duration>",
        "instructions": "<route and special instructions>",
        "quantity": "<total quantity if calculable>"
      }
    ],
    "follow_up_date": "<date>",
    "follow_up_instructions": "<instructions>",
    "clinical_notes": "<doctor notes if any>"
  },
  "patient_instructions": {
    "greeting": "<warm opening — address by patient name>",
    "diagnosis_explained": "<diagnosis in plain language — 1-2 sentences>",
    "medications": [
      {
        "name": "<drug name>",
        "how_to_take": "<simple plain-language instructions>",
        "when_to_take": "<timing>",
        "duration": "<how long>",
        "important_notes": "<side effects to watch for, food interactions, storage>"
      }
    ],
    "follow_up": "<when and why to return>",
    "warning_signs": ["<symptom that means call the doctor immediately>"],
    "closing": "<warm closing note>"
  }
}

Localization rules:
- If locale is NOT "en": translate ALL patient_instructions values into the target language.
- formal_prescription always stays in English.
- Use simple vocabulary in the target language — no medical jargon in patient_instructions.
"""


def prescription_generator_node(state: dict) -> dict:
    medications = state.get("prescribed_medications", [])
    locale = state.get("locale", "en")
    clinic_protocol = state.get("clinic_protocol", {})

    if not medications:
        state["prescription_draft"] = "{}"
        state["sse_log"] = state.get("sse_log", []) + ["[prescription] no medications — skipped"]
        return state

    user_msg = (
        f"Locale: {locale}\n\n"
        f"CLINIC:\n"
        f"  Name: {clinic_protocol.get('clinic_name', 'Clinic')}\n"
        f"  Doctor: {clinic_protocol.get('doctor_name', 'Doctor')}\n"
        f"  Qualifications: {clinic_protocol.get('doctor_qualifications', '')}\n"
        f"  Address: {clinic_protocol.get('clinic_address', '')}\n"
        f"  Phone: {clinic_protocol.get('clinic_phone', '')}\n\n"
        f"PATIENT:\n"
        f"  Name: {state.get('patient_name', 'Patient')}\n"
        f"  DOB: {state.get('patient_dob', '')}\n"
        f"  Contact: {state.get('patient_contact', '')}\n\n"
        f"DIAGNOSIS: {state.get('diagnosis', 'As discussed')}\n"
        f"CLINICAL NOTES: {state.get('doctor_notes', '')}\n\n"
        f"PRESCRIBED MEDICATIONS:\n{json.dumps(medications, indent=2)}\n\n"
        f"FOLLOW-UP DATE: {state.get('follow_up_date', 'As advised')}\n"
        f"FOLLOW-UP INSTRUCTIONS: {state.get('follow_up_instructions', '')}"
    )

    try:
        provider, model = llm_config_for("action")
    except RuntimeError as e:
        state["sse_log"] = state.get("sse_log", []) + [f"[prescription] ERROR: {e}"]
        state["error"] = str(e)
        return state

    result = call_llm(
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=PRESCRIPTION_SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=2000,
    )
    parsed = repair_json(result["text"], provider, model)
    state["prescription_draft"] = json.dumps(parsed, ensure_ascii=False)

    state["model_provider_log"] = state.get("model_provider_log", []) + [{
        "node": "prescription_generator",
        "provider": provider,
        "model": model,
        "tokens": result.get("tokens_used", 0),
    }]

    med_count = len(medications)
    log = state.get("sse_log", [])
    log.append(f"[prescription] {med_count} medication(s) formatted · locale={locale}")
    state["sse_log"] = log
    return state
