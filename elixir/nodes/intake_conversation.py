"""Node 1 — Intake Conversation Agent with Human-in-the-Loop interrupt.
Conducts a full patient intake based on the clinic protocol for the appointment type.
Uses Featherless AI via OpenAI-compatible endpoint.
"""
import json
import env_loader  # noqa: loads elixir/.env
from llm_client import call_llm, repair_json, llm_config_for

INTAKE_SYSTEM_PROMPT = """You are a clinic intake coordinator. Your job is to collect complete patient information before their appointment.

You will receive:
- The patient's initial message and appointment type
- The clinic protocol specifying what questions to ask and what to collect
- Prior follow-up Q&A (may be empty on first call)
- The current follow_up_count

CRITICAL RULES:
- You are NOT a doctor. Never suggest diagnoses, treatments, or medical opinions.
- You are a friendly, professional coordinator collecting information on behalf of the doctor.
- Ask one focused question at a time. Do not overwhelm the patient with multiple questions.
- Be warm and reassuring. Use simple, everyday language.
- If the patient mentions a potential emergency (chest pain, stroke, difficulty breathing, suicidal thoughts), immediately flag it.
- NEVER re-ask a question that has already been answered in "Prior Q&A". If the patient answered "no" or "none", that IS a complete answer — record it and move on.
- An empty list [] in the output means the patient confirmed they have none (e.g., no chronic conditions, no surgeries). This counts as answered.

Decision logic (follow strictly):
1. If all required fields from the protocol are collected → set follow_up_questions to [] and set intake_complete to true.
2. If required fields are missing AND follow_up_count < 2 → emit exactly 1 (ONE) follow_up_question for the MOST IMPORTANT field not yet answered. Do NOT ask about fields already answered in Prior Q&A.
3. If follow_up_count >= 2 → STOP asking questions. Finalize with what you have. Set follow_up_questions to [] and set intake_complete to true.

Output ONLY valid JSON:
{
  "chief_complaint": "<patient's primary concern in their own words>",
  "symptom_timeline": [
    {"symptom": "<symptom>", "onset": "<when it started>", "severity": "<mild|moderate|severe>", "notes": "<any relevant detail>"}
  ],
  "medical_history": {
    "chronic_conditions": [],
    "surgical_history": [],
    "family_history": []
  },
  "current_medications": ["<medication name and dose>"],
  "allergies": ["<allergy>"],
  "missing_required_documents": ["<document type still needed>"],
  "follow_up_questions": [],
  "intake_complete": false,
  "emergency_detected": false,
  "emergency_description": ""
}

Rules:
- follow_up_questions should be conversational and specific to what's still missing.
- If intake_complete is true, follow_up_questions must be [].
- If emergency_detected is true, set emergency_description and follow_up_questions to [].
- chief_complaint must always be populated, even on first call.
- Once the patient has answered a question (even with "no" or "none"), do NOT include it in follow_up_questions.
"""


def _format_protocol_requirements(clinic_protocol: dict, appointment_type: str) -> str:
    if not clinic_protocol:
        return "No specific protocol loaded — collect standard intake: chief complaint, history, medications, allergies."

    apt_types = clinic_protocol.get("appointment_types", [])
    matched = next((a for a in apt_types if a.get("type_id") == appointment_type), None)
    if not matched:
        matched = apt_types[0] if apt_types else {}

    questions = matched.get("required_symptom_questions", [])
    history_fields = matched.get("required_history_fields", [])
    docs = [d.get("label", "") for d in matched.get("required_documents", []) if d.get("required")]
    doc_optional = [d.get("label", "") for d in matched.get("required_documents", []) if not d.get("required")]

    parts = []
    if questions:
        parts.append("Required questions to ask:\n" + "\n".join(f"  - {q}" for q in questions))
    if history_fields:
        parts.append("History fields to collect: " + ", ".join(history_fields))
    if docs:
        parts.append("Required documents: " + ", ".join(docs))
    if doc_optional:
        parts.append("Optional documents (ask if available): " + ", ".join(doc_optional))

    return "\n\n".join(parts) if parts else "Collect: chief complaint, symptoms, history, medications, allergies."


def _format_qa(questions: list, responses: list) -> str:
    if not responses:
        return "No follow-up answers yet."
    pairs = list(zip(questions or [], responses))
    lines = []
    for q, a in pairs:
        lines.append(f"Q: {q}")
        lines.append(f"A: {a}  ← ANSWERED, do not ask again")
    extras = responses[len(pairs):]
    for a in extras:
        lines.append(f"Patient also said: {a}")
    lines.append("")
    lines.append("IMPORTANT: All questions listed above have been answered. Do NOT include them in follow_up_questions.")
    return "\n".join(lines)


_GENERIC_GREETINGS = (
    "hi,", "hi ", "hello", "i'm here to see", "i am here to see",
    "good morning", "good afternoon", "good evening", "just here",
)

_MAIN_CONCERN_Q = "what is your main concern today"


def _is_generic_greeting(msg: str) -> bool:
    lower = (msg or "").lower().strip()
    return len(lower) < 40 or any(phrase in lower for phrase in _GENERIC_GREETINGS)


def _is_main_concern_question(q: str) -> bool:
    return _MAIN_CONCERN_Q in (q or "").lower().strip().rstrip("?")


def intake_conversation_node(state: dict) -> dict:
    clinic_protocol = state.get("clinic_protocol", {})
    appointment_type = state.get("appointment_type", "general_consultation")
    patient_name = state.get("patient_name", "the patient")
    clinic_name = clinic_protocol.get("clinic_name", "the clinic")
    doctor_name = clinic_protocol.get("doctor_name", "the doctor")

    protocol_str = _format_protocol_requirements(clinic_protocol, appointment_type)
    patient_responses = state.get("patient_responses", [])
    follow_up_count = state.get("follow_up_count", 0)
    asked_questions = list(state.get("asked_questions") or state.get("follow_up_questions") or [])
    qa_str = _format_qa(asked_questions, patient_responses)

    # Include any already-extracted document data
    doc_data = state.get("extracted_document_data", [])
    doc_str = ""
    if doc_data:
        doc_str = "\n\nAlready extracted from uploaded documents:\n" + "\n".join(
            f"- {d.get('filename', 'document')}: {d.get('extracted_text', '')[:300]}"
            for d in doc_data
        )

    raw_input = state.get("raw_input", "")
    if patient_responses:
        raw_input_note = (
            "NOTE: The patient has already answered follow-up questions in Prior Q&A above. "
            "Do NOT re-ask any question listed there. Ask the NEXT unanswered protocol question only."
        )
    elif _is_generic_greeting(raw_input) and follow_up_count == 0:
        raw_input_note = (
            "NOTE: The patient's initial message is a generic check-in, not a symptom description. "
            "Your first follow_up_question MUST be 'What is your main concern today?'."
        )
    else:
        raw_input_note = (
            "NOTE: The patient's initial message IS their answer to 'What is your main concern today?' — "
            "do NOT include that question in follow_up_questions."
        )

    user_msg = (
        f"Patient: {patient_name}\n"
        f"Clinic: {clinic_name} | Doctor: {doctor_name}\n"
        f"Appointment type: {appointment_type}\n"
        f"follow_up_count: {follow_up_count}\n\n"
        f"Protocol requirements:\n{protocol_str}\n\n"
        f"Patient's initial message: {raw_input}\n"
        f"{raw_input_note}\n\n"
        f"Prior Q&A:\n{qa_str}"
        f"{doc_str}"
    )

    try:
        provider, model = llm_config_for("triage")
    except RuntimeError as e:
        state["sse_log"] = state.get("sse_log", []) + [f"[intake] ERROR: {e}"]
        state["error"] = str(e)
        return state

    result = call_llm(
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=INTAKE_SYSTEM_PROMPT,
    )
    parsed = repair_json(result["text"], provider, model)

    # Populate intake outputs
    state["chief_complaint"] = parsed.get("chief_complaint", state.get("chief_complaint", ""))
    state["symptom_timeline"] = parsed.get("symptom_timeline", state.get("symptom_timeline", []))
    state["medical_history"] = parsed.get("medical_history", state.get("medical_history", {}))
    state["current_medications"] = parsed.get("current_medications", state.get("current_medications", []))
    state["allergies"] = parsed.get("allergies", state.get("allergies", []))
    state["missing_required_documents"] = parsed.get("missing_required_documents", [])
    state["follow_up_questions"] = parsed.get("follow_up_questions", [])

    # Never re-ask main concern once the patient has answered any follow-up
    if patient_responses:
        state["follow_up_questions"] = [
            q for q in state["follow_up_questions"] if not _is_main_concern_question(q)
        ]

    # Hard cap: after 2 follow-up rounds, force completion regardless of LLM output
    if follow_up_count >= 2:
        state["follow_up_questions"] = []

    # Limit to exactly 1 question per round so Q&A pairing is always 1:1
    if state["follow_up_questions"]:
        state["follow_up_questions"] = state["follow_up_questions"][:1]

    # Track cumulative asked questions for Q&A pairing on subsequent rounds
    for q in state["follow_up_questions"]:
        if q and q not in asked_questions:
            asked_questions.append(q)
    state["asked_questions"] = asked_questions

    # Emergency detection flag
    if parsed.get("emergency_detected"):
        state["emergency_flag"] = True
        state["emergency_reason"] = parsed.get("emergency_description", "Emergency detected during intake")
        state["follow_up_questions"] = []

    state["model_provider_log"] = state.get("model_provider_log", []) + [{
        "node": "intake_conversation",
        "provider": provider,
        "model": model,
        "tokens": result.get("tokens_used", 0),
    }]

    log = state.get("sse_log", [])
    if state["follow_up_questions"]:
        log.append(
            f"[intake] collecting more info — "
            f"{len(state['follow_up_questions'])} question(s) pending"
        )
    else:
        log.append(
            f"[intake] intake complete · chief complaint: {state['chief_complaint'][:60]}"
        )
    state["sse_log"] = log
    return state
