"""Node 3 — Brief Generator. Generates pre-consultation doctor brief from intake record.
Uses Featherless AI via OpenAI-compatible endpoint.
"""
import json
import env_loader  # noqa: loads elixir/.env
from llm_client import call_llm, repair_json, llm_config_for

BRIEF_SYSTEM_PROMPT = """You are a medical scribe generating a pre-consultation brief for a doctor.

You will receive:
- Patient identity (name, age/DOB)
- Appointment type
- Chief complaint and symptom timeline from the intake conversation
- Medical history, current medications, and allergies
- Data extracted from uploaded documents

Your job is to produce a concise, structured doctor brief.

CRITICAL RULES:
- You are NOT making a diagnosis. Do NOT suggest what the condition might be.
- You are NOT recommending treatment. Do NOT suggest medications or procedures.
- You are summarizing what the patient told the intake coordinator and what was found in documents.
- Flag anything the doctor should notice (e.g., allergy to a common medication, prescription 2 years old).
- Be concise — the doctor reads this in under 60 seconds before seeing the patient.
- Language must be clinical but clear. Use standard medical abbreviations (h/o, c/o, k/a, etc.).

Output ONLY valid JSON:
{
  "doctor_brief": "<full structured brief as a formatted string — use newlines for sections>",
  "flags": ["<anything the doctor should specifically notice>"],
  "summary_line": "<one sentence: patient name, age, appointment type, chief complaint>"
}

Brief format (use this structure exactly):
PATIENT: [name] | DOB: [dob] | Age: [calculated age if possible]
APPOINTMENT: [appointment type]
PREPARED: [note this was AI-prepared]

CHIEF COMPLAINT
[chief complaint in patient's words]

SYMPTOM TIMELINE
[each symptom with onset, severity, notes — bullet points]

RELEVANT HISTORY
[chronic conditions, surgical history, family history — relevant items only]
[Current medications: list]
[Known allergies: list — highlight if allergic to common drug classes]

UPLOADED DOCUMENTS
[for each document: filename, doc_type, key extracted values]
[If none: "No documents uploaded"]

FLAGS FOR DOCTOR'S ATTENTION
[Anything notable — old prescription, allergy to common drug, inconsistency in history]
[If nothing notable: "No specific flags"]

INTAKE SUMMARY
[2-3 sentences summarizing the full intake conversation]

---
This brief was prepared by Elixire AI Intake. All clinical decisions rest with the treating physician.
"""


def _format_symptom_timeline(timeline: list) -> str:
    if not timeline:
        return "No symptom timeline recorded."
    lines = []
    for s in timeline:
        parts = [s.get("symptom", "unknown symptom")]
        if s.get("onset"):
            parts.append(f"onset: {s['onset']}")
        if s.get("severity"):
            parts.append(f"severity: {s['severity']}")
        if s.get("notes"):
            parts.append(s["notes"])
        lines.append("• " + " — ".join(parts))
    return "\n".join(lines)


def _format_doc_extractions(docs: list) -> str:
    if not docs:
        return "No documents uploaded."
    lines = []
    for d in docs:
        status = "extracted" if d.get("extracted_text") else "failed (manual review needed)"
        kv = d.get("key_values", {})
        kv_str = ""
        if kv:
            kv_str = " — " + "; ".join(f"{k}: {v}" for k, v in kv.items() if v)
        lines.append(f"✓ {d['filename']} [{d.get('doc_type', 'document')}] ({status}){kv_str}")
    return "\n".join(lines)


def brief_generator_node(state: dict) -> dict:
    patient_name = state.get("patient_name", "Unknown")
    patient_dob = state.get("patient_dob", "Unknown")
    appointment_type = state.get("appointment_type", "general_consultation")
    clinic_protocol = state.get("clinic_protocol", {})
    doctor_name = clinic_protocol.get("doctor_name", "the doctor")

    symptom_str = _format_symptom_timeline(state.get("symptom_timeline", []))
    doc_str = _format_doc_extractions(state.get("extracted_document_data", []))

    history = state.get("medical_history", {})
    medications = state.get("current_medications", [])
    allergies = state.get("allergies", [])
    chief_complaint = state.get("chief_complaint", "Not specified")
    missing_docs = state.get("missing_required_documents", [])

    user_msg = (
        f"PATIENT: {patient_name} | DOB: {patient_dob}\n"
        f"APPOINTMENT TYPE: {appointment_type}\n"
        f"DOCTOR: {doctor_name}\n\n"
        f"CHIEF COMPLAINT:\n{chief_complaint}\n\n"
        f"SYMPTOM TIMELINE:\n{symptom_str}\n\n"
        f"MEDICAL HISTORY:\n{json.dumps(history, indent=2)}\n\n"
        f"CURRENT MEDICATIONS:\n{', '.join(medications) or 'None reported'}\n\n"
        f"KNOWN ALLERGIES:\n{', '.join(allergies) or 'None reported'}\n\n"
        f"UPLOADED DOCUMENTS:\n{doc_str}\n\n"
        f"MISSING DOCUMENTS (requested but not provided):\n"
        f"{', '.join(missing_docs) or 'None'}"
    )

    try:
        provider, model = llm_config_for("synthesis")
    except RuntimeError as e:
        state["sse_log"] = state.get("sse_log", []) + [f"[brief] ERROR: {e}"]
        state["error"] = str(e)
        return state

    result = call_llm(
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=BRIEF_SYSTEM_PROMPT,
    )
    parsed = repair_json(result["text"], provider, model)

    state["doctor_brief"] = parsed.get("doctor_brief", result["text"])

    state["model_provider_log"] = state.get("model_provider_log", []) + [{
        "node": "brief_generator",
        "provider": provider,
        "model": model,
        "tokens": result.get("tokens_used", 0),
    }]

    flags = parsed.get("flags", [])
    log = state.get("sse_log", [])
    log.append(
        f"[brief] doctor brief generated · {len(flags)} flag(s) · "
        f"summary: {parsed.get('summary_line', '')[:80]}"
    )
    state["sse_log"] = log
    return state
