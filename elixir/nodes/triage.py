"""Node 1 — Triage Agent with Human-in-the-Loop interrupt.
Uses Featherless AI via OpenAI-compatible endpoint for structured JSON triage output.
"""
import json, os
import env_loader  # noqa: loads elixir/.env
from llm_client import call_llm, repair_json, llm_config_for

TRIAGE_SYSTEM_PROMPT = """You are a clinical triage assistant with expertise in medical ontology.
Your output must be ONLY valid JSON — no preamble, no markdown, no explanation.

You will receive:
- The patient's reported symptoms
- Their prior medical history (if any) as a timeline of events
- Any follow-up answers already given (may be empty)
- The current follow_up_count and the mode

Decision logic (follow strictly):
1. If triage_confidence >= 0.99: set matched_disease, emit subqueries, set follow_up_questions to [].
2. If triage_confidence < 0.80 AND follow_up_count < 3:
   DO NOT emit subqueries. Set subqueries to [].
   Emit exactly 2 follow_up_questions that would best differentiate between plausible diagnoses.
   These must be specific and answerable by a non-medical person.
3. If follow_up_count >= 3 OR triage_confidence is between 0.80 and 0.99:
   Emit subqueries normally. Set follow_up_questions to [].

Return schema:
{
  "standardized_symptoms": ["<MeSH-normalized term>", ...],
  "mesh_terms": ["<MeSH heading>", ...],
  "subqueries": [
    {
      "search_query": "<3-6 keyword query for PubMed/Semantic Scholar>",
      "ranking_query": "<natural language question for reranking>",
      "mesh_terms": ["<relevant MeSH terms for this subquery>"]
    }
  ],
  "follow_up_questions": ["<question 1>", "<question 2>"],
  "severity": "low|medium|high",
  "triage_confidence": 0.0,
  "matched_disease": null
}

Rules:
- Generate exactly 1 subquery in fast mode, 3 subqueries in deep mode.
- MeSH terms must be valid Medical Subject Headings.
- triage_confidence reflects certainty of matched_disease (0 if no match).
- severity: high if any emergency indicators present (chest pain, stroke signs, etc.).
- Factor in the patient_history_timeline and patient_responses when computing confidence.
"""


def _format_history(timeline: list[dict]) -> str:
    if not timeline:
        return "No prior history provided."
    return "\n".join(
        f"- [{e['date']}] {e['event_type'].upper()}: {e['description']}"
        for e in timeline
    )


def _format_qa(questions: list[str], responses: list[str]) -> str:
    if not responses:
        return "No follow-up answers yet."
    pairs = list(zip(questions or [], responses))
    result = "\n".join(f"Q: {q}\nA: {a}" for q, a in pairs)
    extras = responses[len(pairs):]
    if extras:
        result += "\n" + "\n".join(f"A: {a}" for a in extras)
    return result


def triage_node(state: dict) -> dict:
    mode_hint = "1 subquery" if state["mode"] == "fast" else "3 subqueries"
    history_str = _format_history(state.get("patient_history_timeline", []))
    qa_str = _format_qa(
        state.get("follow_up_questions", []),
        state.get("patient_responses", [])
    )

    user_msg = (
        f"Mode: {state['mode']} ({mode_hint})\n"
        f"follow_up_count: {state.get('follow_up_count', 0)}\n\n"
        f"Symptoms: {state['raw_input']}\n\n"
        f"Patient History:\n{history_str}\n\n"
        f"Prior follow-up Q&A:\n{qa_str}"
    )

    try:
        provider, model = llm_config_for("triage")
    except RuntimeError as e:
        state["sse_log"] = state.get("sse_log", []) + [f"[triage] ERROR: {e}"]
        state["error"] = str(e)
        return state

    if not model:
        state["sse_log"] = state.get("sse_log", []) + [
            "[triage] ERROR: triage model not configured"
        ]
        state["error"] = "Triage model not configured"
        return state

    result = call_llm(
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": user_msg}],
        system=TRIAGE_SYSTEM_PROMPT,
    )
    parsed = repair_json(result["text"], provider, model)

    state["standardized_symptoms"] = parsed.get("standardized_symptoms", [])
    state["mesh_terms"] = parsed.get("mesh_terms", [])
    state["subqueries"] = parsed.get("subqueries", [])
    state["severity"] = parsed.get("severity", "low")
    state["triage_confidence"] = parsed.get("triage_confidence", 0.0)
    state["triage_matched_disease"] = parsed.get("matched_disease")
    state["follow_up_questions"] = parsed.get("follow_up_questions", [])

    # canonical grounding — map symptoms to umls cuis if key available
    try:
        from sources.canonical.umls import ground_to_umls
        state["canonical_terms"] = ground_to_umls(state["standardized_symptoms"])
    except Exception:
        state["canonical_terms"] = state.get("canonical_terms", {})

    # log provider info for sponsor badge rendering
    state["model_provider_log"] = state.get("model_provider_log", []) + [{
        "node": "triage",
        "provider": provider,
        "model": model,
        "tokens": result.get("tokens_used", 0),
    }]

    log = state.get("sse_log", [])
    if state["follow_up_questions"]:
        log.append(
            f"[triage] low confidence ({state['triage_confidence']:.2f}) — "
            f"suspending for {len(state['follow_up_questions'])} follow-up questions"
        )
    else:
        log.append(
            f"[triage] {len(state['subqueries'])} subqueries · "
            f"severity={state['severity']} · confidence={state['triage_confidence']:.2f}"
        )
    state["sse_log"] = log
    return state
