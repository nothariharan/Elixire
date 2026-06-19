"""Elixire shared LangChain tools — wrap node logic for Band agents."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import env_loader  # noqa: F401 — loads elixir/.env


def _base_elixire_state(case: dict) -> dict:
    return {
        # Session
        "session_id": case.get("session_id", ""),
        "clinic_id": case.get("clinic_id", ""),
        "clinic_protocol": case.get("clinic_protocol", {}),
        "appointment_type": case.get("appointment_type", "general_consultation"),
        "patient_id": case.get("patient_id", ""),
        # Patient identity
        "patient_name": case.get("patient_name", ""),
        "patient_dob": case.get("patient_dob", ""),
        "patient_contact": case.get("patient_contact", ""),
        # Input
        "raw_input": case.get("raw_input", ""),
        "locale": case.get("locale", "en"),
        # HITL
        "patient_history_timeline": case.get("patient_history_timeline", []),
        "patient_responses": case.get("patient_responses", []),
        "follow_up_count": case.get("follow_up_count", 0),
        "follow_up_questions": case.get("follow_up_questions", []),
        "asked_questions": case.get("asked_questions", []),
        # Intake outputs (carry forward across rounds)
        "chief_complaint": case.get("chief_complaint", ""),
        "symptom_timeline": case.get("symptom_timeline", []),
        "medical_history": case.get("medical_history", {}),
        "current_medications": case.get("current_medications", []),
        "allergies": case.get("allergies", []),
        # Document state
        "uploaded_documents": case.get("uploaded_documents", []),
        "extracted_document_data": case.get("extracted_document_data", []),
        "missing_required_documents": case.get("missing_required_documents", []),
        # Brief + prescription
        "doctor_brief": case.get("doctor_brief", ""),
        "doctor_notes": case.get("doctor_notes", ""),
        "diagnosis": case.get("diagnosis", ""),
        "prescribed_medications": case.get("prescribed_medications", []),
        "follow_up_date": case.get("follow_up_date", ""),
        "follow_up_instructions": case.get("follow_up_instructions", ""),
        "prescription_draft": case.get("prescription_draft", ""),
        "prescription_verified": case.get("prescription_verified", False),
        "prescription_pdf_path": case.get("prescription_pdf_path", ""),
        # Telemetry
        "sse_log": case.get("sse_log", []),
        "model_provider_log": case.get("model_provider_log", []),
        "provenance": case.get("provenance", []),
        "error": case.get("error"),
        "latency_ms": case.get("latency_ms", 0),
        # Routing
        "is_valid": case.get("is_valid", False),
        "emergency_flag": case.get("emergency_flag", False),
        "emergency_reason": case.get("emergency_reason"),
    }


def _pick(state: dict, *keys: str) -> dict:
    return {k: state[k] for k in keys if k in state}


def _load_json_arg(s: str) -> dict:
    """Parse a JSON tool argument tolerantly — LLMs sometimes append extra text."""
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # raw_decode stops at the first complete object, ignoring trailing content
        obj, _ = json.JSONDecoder().raw_decode(s)
        return obj


# ── Elixire-Receptionist tools ─────────────────────────────────────────────────

@tool
def run_receptionist(case_json: str) -> str:
    """Validate appointment context and route patient to intake. No LLM call."""
    from nodes.session_router import session_router_node

    case = _load_json_arg(case_json)
    state = _base_elixire_state(case)
    state = session_router_node(state)
    payload = _pick(state, "is_valid", "appointment_type", "patient_name", "sse_log", "error")
    payload["elixire_status"] = "ready_for_intake" if state.get("is_valid") else "invalid"
    return json.dumps(payload, default=str)


# ── Elixire-Intake tools ────────────────────────────────────────────────────────

@tool
def run_intake_conversation(case_json: str) -> str:
    """Conduct patient intake conversation using clinic protocol. HITL-aware."""
    from nodes.intake_conversation import intake_conversation_node

    case = _load_json_arg(case_json)
    state = _base_elixire_state(case)
    state = intake_conversation_node(state)

    payload = _pick(
        state,
        "chief_complaint",
        "symptom_timeline",
        "medical_history",
        "current_medications",
        "allergies",
        "follow_up_questions",
        "asked_questions",
        "missing_required_documents",
        "emergency_flag",
        "emergency_reason",
        "sse_log",
        "model_provider_log",
        "error",
    )
    if state.get("follow_up_questions"):
        payload["elixire_status"] = "hitl"
    elif state.get("emergency_flag"):
        payload["elixire_status"] = "emergency"
    else:
        payload["elixire_status"] = "ready_for_documents"
    return json.dumps(payload, default=str)


@tool
def run_document_processor(case_json: str) -> str:
    """Process uploaded patient documents in parallel (PDF + OCR)."""
    from nodes.document_processor import document_processor_node

    case = _load_json_arg(case_json)
    state = _base_elixire_state(case)
    state = document_processor_node(state)

    payload = _pick(
        state,
        "extracted_document_data",
        "missing_required_documents",
        "sse_log",
        "error",
    )
    return json.dumps(payload, default=str)


# ── Elixire-Brief tools ─────────────────────────────────────────────────────────

@tool
def run_brief_generator(case_json: str) -> str:
    """Generate pre-consultation doctor brief from complete intake record."""
    from nodes.brief_generator import brief_generator_node

    case = _load_json_arg(case_json)
    state = _base_elixire_state(case)
    state = brief_generator_node(state)

    payload = _pick(
        state,
        "doctor_brief",
        "patient_name",
        "appointment_type",
        "sse_log",
        "model_provider_log",
        "error",
    )
    payload["elixire_status"] = "brief_ready"
    return json.dumps(payload, default=str)


@tool
def run_prescription_verifier(case_json: str) -> str:
    """Verify prescription completeness and allergy conflicts via AML API."""
    from nodes.prescription_verifier import prescription_verifier_node

    case = _load_json_arg(case_json)
    state = _base_elixire_state(case)
    state = prescription_verifier_node(state)

    payload = _pick(
        state,
        "prescription_verified",
        "prescribed_medications",
        "provenance",
        "sse_log",
        "model_provider_log",
        "error",
    )
    verified_by = "featherless-fallback"
    for entry in state.get("model_provider_log", []):
        if entry.get("node") == "prescription_verifier":
            verified_by = entry.get("verified_by", verified_by)
    payload["verified_by"] = verified_by
    return json.dumps(payload, default=str)


@tool
def run_prescription_generator(case_json: str) -> str:
    """Generate formatted prescription document and patient-friendly instructions."""
    from nodes.prescription_generator import prescription_generator_node

    case = _load_json_arg(case_json)
    state = _base_elixire_state(case)
    state = prescription_generator_node(state)

    payload = _pick(
        state,
        "prescription_draft",
        "sse_log",
        "model_provider_log",
        "error",
    )
    payload["elixire_status"] = "complete"
    payload["prescription_verified"] = case.get("prescription_verified", False)
    payload["provenance"] = case.get("provenance", [])
    payload["doctor_brief"] = case.get("doctor_brief", "")
    return json.dumps(payload, default=str)


# ── Legacy Elixir v4 tools (kept for backward compat with legacy graph mode) ────

@tool
def validate_input(raw_input: str) -> str:
    """validate that input contains medical symptom keywords (legacy)."""
    from nodes.guard import guard_node
    state = guard_node({"raw_input": raw_input, "sse_log": []})
    return json.dumps({"is_valid": state["is_valid"], "error": state.get("error")})


@tool
def run_triage(case_json: str) -> str:
    """run featherless triage on a case payload json string (legacy)."""
    from nodes.triage import triage_node
    case = _load_json_arg(case_json)
    state = {
        "raw_input": case.get("raw_input", ""),
        "mode": case.get("mode", "fast"),
        "locale": case.get("locale", "en"),
        "thread_id": case.get("thread_id", ""),
        "patient_history_timeline": case.get("patient_history_timeline", []),
        "patient_responses": case.get("patient_responses", []),
        "follow_up_count": case.get("follow_up_count", 0),
        "follow_up_questions": case.get("follow_up_questions", []),
        "sse_log": case.get("sse_log", []),
        "model_provider_log": case.get("model_provider_log", []),
        "provenance": case.get("provenance", []),
        "canonical_terms": case.get("canonical_terms", {}),
        "rate_limited_sources": set(),
    }
    state = triage_node(state)
    payload = _pick(state, "standardized_symptoms", "mesh_terms", "subqueries",
                    "follow_up_questions", "severity", "triage_confidence",
                    "triage_matched_disease", "canonical_terms", "sse_log",
                    "model_provider_log", "error")
    payload["elixir_status"] = "hitl" if state.get("follow_up_questions") else "ready_for_clinical"
    return json.dumps(payload, default=str)
