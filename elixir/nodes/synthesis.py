"""Node 3 — Synthesis Agent. Generates differential diagnosis from research snippets.
Uses Featherless AI via OpenAI-compatible endpoint.
"""
import json, os
import env_loader  # noqa: loads elixir/.env
from llm_client import call_llm, repair_json, llm_config_for

SYNTHESIS_SYSTEM_PROMPT = """You are a clinical synthesis specialist. You will receive:
1. The patient's standardized symptoms.
2. Their medical history timeline (from uploaded records).
3. Their answers to follow-up questions from the triage phase.
4. A bundle of de-duplicated medical research snippets, each tagged with a source_tier.

Your job is to produce a ranked list of conditions worth discussing with a licensed physician,
based on how well the patient's reported symptoms, history, and follow-up answers match each
condition. This is NOT a diagnosis — it is a research summary to facilitate informed conversation
with a healthcare provider.

Weight the patient's history and follow-up answers heavily — they are first-person evidence.
Research snippets provide population-level evidence; history/Q&A provide individual context.

IMPORTANT SOURCE TIER RULES:
- Snippets tagged "canonical" (UMLS, RxNorm, DailyMed) are ground truth — highest priority.
- Snippets tagged "literature" (PubMed, Europe PMC, etc.) are evidence — can be cited.
- Snippets tagged "contextual" (Reddit, Wikipedia, web search) may inform your understanding
  but MUST NOT be cited as the "source" field for any condition. Only "canonical" or "literature"
  tier snippets may be cited.

Output ONLY valid JSON:
{
  "confidence_spread": [
    { "condition": "<condition name>", "probability": 0.XX, "source": "<source_id>" },
    { "condition": "<condition name>", "probability": 0.XX, "source": "<source_id>" },
    { "condition": "<condition name>", "probability": 0.XX, "source": "<source_id>" }
  ],
  "synthesis_reasoning": "<one sentence explaining the top pick, referencing patient context if relevant>"
}

Rules:
- Exactly 3 conditions. Probabilities must sum to 1.0.
- source must be a source_id from canonical or literature tier snippets.
  If no canonical/literature snippets support a condition, use "insufficient_evidence".
- If patient history directly supports or rules out a condition, adjust probability accordingly.
"""


def synthesis_node(state: dict) -> dict:
    snippets = state.get("clustered_snippets", [])

    # build snippet text with tier information
    snippet_text = "\n---\n".join(
        f"[{s['source_id']}] ({s['source_name']}) [tier: {s.get('source_tier', 'unknown')}]\n{s['text']}"
        for s in snippets
    ) or "No research snippets available."

    # build patient context
    history_events = state.get("patient_history_timeline", [])
    history_str = "\n".join(
        f"- [{e['date']}] {e['event_type'].upper()}: {e['description']}"
        for e in history_events
    ) or "No prior history provided."

    qa_responses = state.get("patient_responses", [])
    qa_str = "\n".join(f"- {r}" for r in qa_responses) or "No follow-up answers provided."

    symptoms = state.get("standardized_symptoms", [])
    symptoms_str = ", ".join(symptoms) if symptoms else state.get("raw_input", "unknown")

    user_msg = (
        f"Symptoms: {symptoms_str}\n\n"
        f"Patient Medical History:\n{history_str}\n\n"
        f"Patient Follow-up Answers:\n{qa_str}\n\n"
        f"Research snippets:\n{snippet_text}"
    )

    try:
        provider, model = llm_config_for("synthesis")
    except RuntimeError as e:
        state["sse_log"] = state.get("sse_log", []) + [f"[synthesis] ERROR: {e}"]
        state["error"] = str(e)
        return state

    if not model:
        state["sse_log"] = state.get("sse_log", []) + [
            "[synthesis] ERROR: synthesis model not configured"
        ]
        state["error"] = "Synthesis model not configured"
        return state

    result = call_llm(
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=SYNTHESIS_SYSTEM_PROMPT,
    )
    parsed = repair_json(result["text"], provider, model)

    state["confidence_spread"] = parsed.get("confidence_spread", [])
    state["synthesis_draft"] = result["text"]

    # log provider for sponsor badge
    state["model_provider_log"] = state.get("model_provider_log", []) + [{
        "node": "synthesis",
        "provider": provider,
        "model": model,
        "tokens": result.get("tokens_used", 0),
    }]

    log = state.get("sse_log", [])
    if state["confidence_spread"]:
        top = state["confidence_spread"][0]
        log.append(
            f"[synthesis] top condition: {top['condition']} ({top['probability']:.0%})"
        )
    else:
        log.append("[synthesis] no conditions generated")
    state["sse_log"] = log
    return state
