"""Node 4 — Verification Node. Cross-checks claims against raw source text.
Uses AML API via OpenAI-compatible endpoint, with Featherless fallback.
This is the SINGLE AML-credit call in the entire pipeline.
"""
import json, os, logging
import env_loader  # noqa: loads elixir/.env
from llm_client import (
    call_llm,
    repair_json,
    llm_config_for,
    verification_fallback_config,
    AML_MODEL_VERIFICATION,
)

logger = logging.getLogger("elixir.verification")

VERIFICATION_SYSTEM_PROMPT = """You are a medical fact-checker. You will receive:
1. A proposed differential diagnosis with cited sources.
2. The raw source text those citations came from, each tagged with a source_tier.

For each condition in the diagnosis:
- Verify that the source text actually supports the condition claim.
- If it does NOT: remove the condition from the output and redistribute probabilities so they sum to 1.0.

Output ONLY valid JSON:
{
  "confidence_spread": [
    { "condition": "<name>", "probability": 0.XX, "source": "<source_id>" }
  ],
  "verified": true,
  "removed": [],
  "provenance": [
    {
      "claim": "<the specific claim being made>",
      "source_id": "<matches a provided source_id>",
      "source_tier": "canonical|literature",
      "matched_sentence": "<exact sentence from the source text that supports this claim>",
      "canonical_code": "<UMLS CUI or null>"
    }
  ]
}

Rules:
- Every condition in confidence_spread must have at least one corresponding provenance entry.
- matched_sentence must be copied verbatim from the provided source text — if you cannot find a
  supporting sentence, remove the claim instead of inventing one.
- contextual-tier sources (Reddit, Wikipedia, web search) must never appear in provenance.
- If all claims are verified, set "verified": true. If any were removed, set "verified": false.
- Probabilities must always sum to 1.0 after redistribution.
"""


def verification_node(state: dict) -> dict:
    snippets = state.get("clustered_snippets", [])

    # include tier info in the source text bundle
    raw_text_bundle = "\n---\n".join(
        f"[{s['source_id']}] [tier: {s.get('source_tier', 'unknown')}]: {s['text']}"
        for s in snippets
    ) or "No source text available."

    draft = json.dumps(state.get("confidence_spread", []), indent=2)

    # include canonical terms if available
    canonical = state.get("canonical_terms", {})
    canonical_str = ""
    if canonical:
        canonical_str = "\n\nCanonical term mappings (UMLS CUIs):\n" + "\n".join(
            f"- {term}: {cui}" for term, cui in canonical.items()
        )

    user_msg = (
        f"Diagnosis draft:\n{draft}\n\n"
        f"Raw source text:\n{raw_text_bundle}"
        f"{canonical_str}"
    )

    try:
        provider, model = llm_config_for("verification")
    except RuntimeError as e:
        state["sse_log"] = state.get("sse_log", []) + [f"[verify] ERROR: {e}"]
        state["error"] = str(e)
        return state

    if provider == "aml":
        verified_by = "aml"
    elif provider == "bedrock":
        verified_by = "bedrock"
    elif not AML_MODEL_VERIFICATION:
        verified_by = "featherless-fallback"
        logger.warning("AML_MODEL_VERIFICATION not configured — using Featherless fallback")
        state["sse_log"] = state.get("sse_log", []) + [
            "[verify] AML not configured — using Featherless fallback"
        ]
    else:
        verified_by = provider

    try:
        result = call_llm(
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": user_msg}],
            system=VERIFICATION_SYSTEM_PROMPT,
            max_tokens=800,
        )
    except Exception as e:
        if provider == "aml":
            fb_provider, fb_model = verification_fallback_config()
            logger.warning(f"AML call failed ({e}) — falling back to {fb_provider}")
            provider, model = fb_provider, fb_model
            verified_by = f"{fb_provider}-fallback"

            state["sse_log"] = state.get("sse_log", []) + [
                f"[verify] AML failed — using {fb_provider} fallback"
            ]

            result = call_llm(
                provider=provider,
                model=model,
                messages=[{"role": "user", "content": user_msg}],
                system=VERIFICATION_SYSTEM_PROMPT,
                max_tokens=800,
            )
        else:
            state["sse_log"] = state.get("sse_log", []) + [f"[verify] ERROR: {e}"]
            state["error"] = f"Verification failed: {e}"
            return state

    parsed = repair_json(result["text"], provider, model)

    state["final_confidence_spread"] = parsed.get(
        "confidence_spread", state.get("confidence_spread", [])
    )
    state["verified"] = parsed.get("verified", False)
    removed = parsed.get("removed", [])

    # populate provenance with verified_by field
    raw_provenance = parsed.get("provenance", [])
    state["provenance"] = [
        {
            "claim": p.get("claim", ""),
            "source_id": p.get("source_id", ""),
            "source_tier": p.get("source_tier", ""),
            "matched_sentence": p.get("matched_sentence", ""),
            "canonical_code": p.get("canonical_code"),
            "verified_by": verified_by,
        }
        for p in raw_provenance
    ]

    # log provider for sponsor badge
    state["model_provider_log"] = state.get("model_provider_log", []) + [{
        "node": "verification",
        "provider": provider,
        "model": model,
        "tokens": result.get("tokens_used", 0),
        "verified_by": verified_by,
    }]

    log = state.get("sse_log", [])
    log.append(
        f"[verify] {'✓ all claims verified' if state['verified'] else f'⚠ removed: {removed}'}"
        f" · verified_by={verified_by}"
    )
    state["sse_log"] = log
    return state
