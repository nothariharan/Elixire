"""Node 5 — Action Translation Agent. Patient-facing action plan.
Uses Featherless AI via OpenAI-compatible endpoint.
"""
import json, os
import env_loader  # noqa: loads elixir/.env
from llm_client import call_llm, repair_json, llm_config_for

ACTION_SYSTEM_PROMPT = """You are a patient advocate and medical interpreter. You will receive:
1. A verified differential diagnosis (top 3 conditions with probabilities).
2. The patient's medical history timeline.
3. Provenance records showing which sources support each claim.
4. A target output locale (BCP-47 code, e.g. "en").

Your job is to produce a compassionate, non-clinical, patient-facing action plan.
This is NOT a diagnosis — it is a guide for what to discuss with a licensed physician.

Output ONLY valid JSON:
{
  "action_plan": {
    "summary": "<2-3 sentences explaining the most likely condition in plain language>",
    "tests_to_request": [
      "<specific test name>: <why the patient should ask for this> (supported by: [<source_id>])",
      ...
    ],
    "red_flags": [
      "<symptom that means go to emergency immediately>",
      ...
    ],
    "next_steps": "<plain-language paragraph of what to do next>",
    "disclaimer": "This is an AI-generated research summary, not a medical diagnosis. Always consult a licensed physician."
  }
}

Rules:
- Use simple, everyday vocabulary. Avoid Latin medical terms unless followed by a plain-language
  explanation in parentheses.
- tests_to_request: list exactly 3-5 specific tests ordered by diagnostic priority for the top
  condition. Reference provenance source_ids where applicable.
- red_flags: list 2-3 emergency symptoms. Be conservative — only include genuinely alarming signs.
- The disclaimer is mandatory and must always be included verbatim.

Localization rules:
- If locale is NOT "en": translate the ENTIRE JSON output values into the target language.
  JSON keys remain in English. Do not mix languages.
- Use simple, everyday vocabulary in the target language.
"""


def action_node(state: dict) -> dict:
    spread = state.get("final_confidence_spread", state.get("confidence_spread", []))
    locale = state.get("locale", "en")

    history_events = state.get("patient_history_timeline", [])
    history_str = "\n".join(
        f"- [{e['date']}] {e['event_type'].upper()}: {e['description']}"
        for e in history_events
    ) or "No prior history provided."

    # include provenance for action plan context
    provenance = state.get("provenance", [])
    provenance_str = ""
    if provenance:
        provenance_str = "\n\nProvenance (source evidence for each claim):\n" + "\n".join(
            f"- {p['claim']}: [{p['source_id']}] \"{p['matched_sentence'][:100]}...\""
            for p in provenance[:10]
        )

    user_msg = (
        f"Locale: {locale}\n\n"
        f"Differential Diagnosis:\n{json.dumps(spread, indent=2)}\n\n"
        f"Verified: {state.get('verified', False)}\n\n"
        f"Patient History:\n{history_str}"
        f"{provenance_str}"
    )

    try:
        provider, model = llm_config_for("action")
    except RuntimeError as e:
        state["sse_log"] = state.get("sse_log", []) + [f"[action] ERROR: {e}"]
        state["error"] = str(e)
        return state

    if not model:
        state["sse_log"] = state.get("sse_log", []) + [
            "[action] ERROR: action model not configured"
        ]
        state["error"] = "Action model not configured"
        return state

    result = call_llm(
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=ACTION_SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=1500,
    )
    parsed = repair_json(result["text"], provider, model)
    state["action_plan_draft"] = json.dumps(parsed.get("action_plan", {}), ensure_ascii=False)

    # log provider for sponsor badge
    state["model_provider_log"] = state.get("model_provider_log", []) + [{
        "node": "action",
        "provider": provider,
        "model": model,
        "tokens": result.get("tokens_used", 0),
    }]

    log = state.get("sse_log", [])
    log.append(
        f"[action] patient plan generated · locale={locale} · "
        f"tests={len(parsed.get('action_plan', {}).get('tests_to_request', []))}"
    )
    state["sse_log"] = log
    return state
