"""Node 0.5 — Emergency Gate. Pure Python, zero LLM calls, zero network calls.
Inserted between Guard and Triage. If triggered, the graph short-circuits to END
with an emergency-care message. This is the single most important reliability
feature — it must work even if every API key is missing.
"""

# each pattern is (required_keywords, optional_co_occurring_keywords, category)
# a match requires all required keywords and at least one co-occurring keyword
# (unless co-occurring is empty, meaning required alone is sufficient).
EMERGENCY_PATTERNS = [
    # cardiac emergency
    (
        {"chest pain"},
        {"shortness of breath", "breathless", "can't breathe", "cannot breathe",
         "radiating", "left arm", "jaw pain", "crushing"},
        "cardiac",
    ),
    # stroke signs
    (
        {"sudden"},
        {"worst headache", "vision loss", "confusion", "slurred speech",
         "weakness on one side", "face drooping", "arm weakness", "can't speak"},
        "stroke",
    ),
    # severe gi emergency
    (
        {"severe abdominal pain"},
        {"vomiting blood", "black stool", "blood in stool", "hematemesis"},
        "gi_emergency",
    ),
    # seizure emergency
    (
        {"seizure"},
        {"first time", "not stopping", "won't wake up", "continuous", "status epilepticus"},
        "seizure",
    ),
    # anaphylaxis
    (
        {"allergic reaction"},
        {"swelling throat", "can't breathe", "cannot breathe", "tongue swelling", "epipen"},
        "anaphylaxis",
    ),
    # suicidal ideation — no co-occurring needed, any single trigger suffices
    (
        {"suicidal", "want to die", "end my life", "kill myself", "suicide"},
        set(),
        "crisis",
    ),
]

# crisis line resources — verify numbers for your demo region before presenting
CRISIS_RESOURCES = {
    "message": (
        "If you or someone you know is in immediate danger, please contact "
        "emergency services or a crisis helpline right away."
    ),
    "helplines": [
        {"name": "Emergency Services", "number": "911 (US) / 112 (EU) / 108 (India)"},
        {"name": "Crisis Text Line", "number": "Text HOME to 741741 (US)"},
        {"name": "iCall (India)", "number": "9152987821"},
        {"name": "AASRA (India)", "number": "9820466726"},
        {"name": "988 Suicide & Crisis Lifeline (US)", "number": "988"},
    ],
}

EMERGENCY_MESSAGE = (
    "⚠️ EMERGENCY: Based on the symptoms described, this may require immediate "
    "medical attention. Please call emergency services (911/112/108) or go to "
    "the nearest emergency room immediately. Do NOT wait for an online assessment."
)


def emergency_node(state: dict) -> dict:
    """Deterministic emergency pattern matching. No LLM, no network.
    Sets emergency_flag=True if emergency patterns detected.
    """
    text = state.get("raw_input", "").lower()
    log = state.get("sse_log", [])

    for required_kws, co_occurring_kws, category in EMERGENCY_PATTERNS:
        # check if any of the required keywords appear in the text
        has_required = any(kw in text for kw in required_kws)
        if not has_required:
            continue

        # if co-occurring set is empty, required alone is sufficient (e.g., suicidal ideation)
        if not co_occurring_kws or any(kw in text for kw in co_occurring_kws):
            state["emergency_flag"] = True
            matched = required_kws | ({kw for kw in co_occurring_kws if kw in text} if co_occurring_kws else set())
            state["emergency_reason"] = f"Detected {category} pattern: {matched}"

            if category == "crisis":
                log.append("[emergency] CRISIS pattern detected — providing crisis resources")
            else:
                log.append(f"[emergency] CRITICAL {category} pattern detected — bypassing pipeline")

            state["sse_log"] = log
            return state

    state["emergency_flag"] = False
    state["emergency_reason"] = None
    log.append("[emergency] no emergency patterns detected — proceeding to triage")
    state["sse_log"] = log
    return state
