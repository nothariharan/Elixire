"""Node 0 — Session Router. Validates appointment type against clinic protocol. No LLM."""
from clinic_protocol.schema import ClinicProtocol

VALID_APPOINTMENT_KEYWORDS = {
    "eye", "vision", "checkup", "check-up", "consultation", "appointment",
    "pain", "fever", "rash", "cough", "injury", "follow-up", "followup",
    "review", "prescription", "refill", "general", "emergency", "routine",
}


def session_router_node(state: dict) -> dict:
    raw = state.get("raw_input", "").lower()
    appointment_type = state.get("appointment_type", "")
    clinic_protocol = state.get("clinic_protocol", {})

    # Check appointment type is valid in protocol (if protocol loaded)
    if clinic_protocol and appointment_type:
        protocol_obj = ClinicProtocol(**clinic_protocol)
        matched = protocol_obj.get_appointment_type(appointment_type)
        if matched:
            state["is_valid"] = True
            state["sse_log"] = state.get("sse_log", []) + [
                f"[router] appointment type '{appointment_type}' validated against protocol"
            ]
            return state

    # Fallback: keyword check on raw_input for demo/no-protocol mode
    import re
    tokens = set(re.findall(r'\b\w+\b', raw))
    has_health_keyword = bool(tokens & VALID_APPOINTMENT_KEYWORDS)
    patient_name = state.get("patient_name", "")
    has_name = bool(patient_name and len(patient_name) > 1)

    state["is_valid"] = has_health_keyword or has_name
    if state["is_valid"]:
        state["sse_log"] = state.get("sse_log", []) + ["[router] session validated"]
    else:
        state["sse_log"] = state.get("sse_log", []) + [
            "[router] rejected: could not determine appointment context"
        ]
        state["error"] = "Could not determine appointment type. Please describe why you are visiting."
    return state
