"""Node 0 — Input Guard. No LLM call. Pure regex + keyword check."""
import re

MEDICAL_KEYWORDS = {
    "fever", "pain", "rash", "cough", "fatigue", "nausea", "headache",
    "swelling", "bleeding", "shortness", "breath", "vomiting", "diarrhea",
    "dizziness", "numbness", "seizure", "chest", "abdomen", "symptom",
    "joint", "muscle", "skin", "throat", "discharge", "lesion", "bruise",
}

MIN_MATCH = 1


def guard_node(state: dict) -> dict:
    text = state["raw_input"].lower()
    tokens = set(re.findall(r'\b\w+\b', text))
    matches = tokens & MEDICAL_KEYWORDS
    state["is_valid"] = len(matches) >= MIN_MATCH
    if state["is_valid"]:
        state["sse_log"] = state.get("sse_log", []) + ["[guard] input validated"]
    else:
        state["sse_log"] = state.get("sse_log", []) + ["[guard] rejected: no medical content detected"]
        state["error"] = "Input does not appear to contain medical symptoms."
    return state
