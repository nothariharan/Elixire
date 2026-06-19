"""map band messages/events to elixire sse_log and response shapes."""
from __future__ import annotations

import json
from typing import Any

from gateway.config import AGENT_NAMES

SPECIALIST_MENTIONS = {
    "@elixire-intake", "@elixire-brief",
    "elixire-intake", "elixire-brief",
    AGENT_NAMES["intake"].lower(), AGENT_NAMES["brief"].lower(),
    "@elixir-clinical", "@elixir-action",
    "elixir-clinical", "elixir-action",
}

AGENT_SSE_PREFIX = {
    AGENT_NAMES["receptionist"].lower(): "receptionist",
    "elixire-receptionist": "receptionist",
    AGENT_NAMES["intake"].lower(): "intake",
    "elixire-intake": "intake",
    AGENT_NAMES["brief"].lower(): "brief",
    "elixire-brief": "brief",
    AGENT_NAMES["gateway"].lower(): "system",
}


def extract_json_payload(content: str) -> dict | None:
    """Find the first valid JSON object in message content (handles nested structures)."""
    # Fast path: entire content is JSON
    try:
        data = json.loads(content.strip())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Scan for first '{' and try raw_decode — works for "@Agent {json...}" messages
    i = 0
    while i < len(content):
        idx = content.find("{", i)
        if idx < 0:
            break
        try:
            obj, _ = json.JSONDecoder().raw_decode(content[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = idx + 1

    return None


def message_to_sse(content: str, sender_name: str | None) -> str:
    agent = (sender_name or "band").lower()
    prefix = "system"
    for key, cat in AGENT_SSE_PREFIX.items():
        if key in agent:
            prefix = cat
            break
    short = content[:200].replace("\n", " ")
    return f"[{prefix}] {sender_name or 'agent'}: {short}"


def detect_handoff(content: str) -> str | None:
    lower = content.lower()
    if any(x in lower for x in ("@elixire-intake", "elixire-intake", "elixir-clinical", "@elixir-clinical")):
        return "Receptionist -> Intake"
    if any(x in lower for x in ("@elixire-brief", "elixire-brief", "elixir-action", "@elixir-action")):
        return "Intake -> Brief"
    if "doctor_brief" in lower and ("complete" in lower or "elixire_status" in lower):
        return "Brief -> Doctor"
    return None


def is_hitl_message(content: str, sender_name: str | None) -> bool:
    payload = extract_json_payload(content)
    if payload and payload.get("elixire_status") == "hitl":
        return True
    if payload and payload.get("follow_up_questions"):
        return True
    sender = (sender_name or "").lower()
    if "intake" not in sender:
        return False
    lower = content.lower()
    if any(m in lower for m in SPECIALIST_MENTIONS):
        return False
    return "?" in content or "follow-up" in lower or "follow up" in lower


def is_complete_message(content: str, sender_name: str | None) -> bool:
    payload = extract_json_payload(content)
    if payload and payload.get("elixire_status") == "complete":
        return True
    if payload and payload.get("doctor_brief"):
        return True
    sender = (sender_name or "").lower()
    # sender may be "elixir-action" (new) or "elixire-brief" (old)
    is_brief = "brief" in sender or "action" in sender
    return is_brief and ("doctor_brief" in content.lower() or "elixire_status" in content.lower())


def build_result_from_payload(payload: dict, thread_id: str, latency_ms: int) -> dict[str, Any]:
    audit = {
        "model_provider_log": payload.get("model_provider_log", []),
        "provenance": payload.get("provenance", []),
    }
    return {
        "thread_id": thread_id,
        "status": "complete",
        "doctor_brief": payload.get("doctor_brief", ""),
        "prescription_draft": payload.get("prescription_draft", ""),
        "prescription_verified": payload.get("prescription_verified", False),
        "sse_log": payload.get("sse_log", []),
        "model_provider_log": payload.get("model_provider_log", []),
        "audit_trail": audit,
        "latency_ms": latency_ms,
    }


def build_brief_response(payload: dict, thread_id: str, latency_ms: int) -> dict[str, Any]:
    """Build response when pre-consultation brief is ready for the doctor."""
    return {
        "thread_id": thread_id,
        "status": "brief_ready",
        "doctor_brief": payload.get("doctor_brief", ""),
        "patient_name": payload.get("patient_name", ""),
        "appointment_type": payload.get("appointment_type", ""),
        "sse_log": payload.get("sse_log", []),
        "model_provider_log": payload.get("model_provider_log", []),
        "latency_ms": latency_ms,
    }


def build_prescription_response(payload: dict, thread_id: str, latency_ms: int) -> dict[str, Any]:
    """Build response after prescription generation."""
    return {
        "thread_id": thread_id,
        "status": "prescription_ready",
        "prescription_draft": payload.get("prescription_draft", ""),
        "prescription_verified": payload.get("prescription_verified", False),
        "prescription_pdf_path": payload.get("prescription_pdf_path", ""),
        "model_provider_log": payload.get("model_provider_log", []),
        "latency_ms": latency_ms,
    }


def build_hitl_response(payload: dict, thread_id: str, sse_log: list) -> dict[str, Any]:
    questions = payload.get("follow_up_questions", [])
    if not questions and isinstance(payload.get("questions"), list):
        questions = payload["questions"]
    return {
        "status": "requires_action",
        "thread_id": thread_id,
        "questions": questions,
        "sse_log": sse_log,
        "model_provider_log": payload.get("model_provider_log", []),
    }
