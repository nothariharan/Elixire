"""Node 4 — Prescription Verifier. Safety check on doctor-entered prescription.
Uses AML API via OpenAI-compatible endpoint, with Featherless fallback.
This is the SINGLE AML-credit call in the entire pipeline — mirrors Elixir v4.0's verification node.
"""
import json
import logging
import env_loader  # noqa: loads elixir/.env
from llm_client import (
    call_llm,
    repair_json,
    llm_config_for,
    verification_fallback_config,
    AML_MODEL_VERIFICATION,
)

logger = logging.getLogger("elixire.prescription_verifier")

PRESCRIPTION_VERIFICATION_PROMPT = """You are a clinical pharmacist safety reviewer.

You will receive:
1. The patient's known allergies
2. The list of medications prescribed by the doctor (name, dosage, frequency, duration, instructions)

Your job is a SAFETY CHECK ONLY. You are NOT altering the prescription — the doctor's decision is final.

Check for:
- Incomplete dosage instructions (missing frequency, duration, or route of administration)
- Obvious allergy conflicts (patient is allergic to a drug class, and the prescribed drug belongs to that class)
- Potentially missing instructions (e.g., "take with food" for GI-irritating drugs)
- Format issues (e.g., dosage not specified as a unit)

Do NOT flag:
- Drug choices (the doctor has clinical reasons)
- Drug interactions (complex — out of scope for this check)
- Off-label use

Output ONLY valid JSON:
{
  "prescription_verified": true,
  "flags": [
    {
      "medication": "<drug name>",
      "flag_type": "allergy_conflict|incomplete_instructions|missing_unit|other",
      "description": "<clear description of the issue>",
      "severity": "warning|critical"
    }
  ],
  "verified_medications": [
    {
      "name": "<drug name>",
      "dosage": "<dosage>",
      "frequency": "<frequency>",
      "duration": "<duration>",
      "instructions": "<patient-friendly instructions>",
      "status": "ok|flagged"
    }
  ],
  "verification_summary": "<one sentence summary>"
}

Rules:
- prescription_verified is true if there are NO critical flags.
- Warnings do not prevent verification — they are informational.
- verified_medications must include ALL prescribed medications, each with a status.
- If instructions are empty, generate patient-friendly instructions based on drug class (e.g., "Take with food").
"""


def prescription_verifier_node(state: dict) -> dict:
    medications = state.get("prescribed_medications", [])
    allergies = state.get("allergies", [])

    if not medications:
        state["prescription_verified"] = True
        state["sse_log"] = state.get("sse_log", []) + ["[verify] no medications to verify"]
        return state

    user_msg = (
        f"Patient known allergies: {', '.join(allergies) or 'None'}\n\n"
        f"Prescribed medications:\n{json.dumps(medications, indent=2)}"
    )

    try:
        provider, model = llm_config_for("verification")
    except RuntimeError as e:
        state["sse_log"] = state.get("sse_log", []) + [f"[verify] ERROR: {e}"]
        state["error"] = str(e)
        return state

    if provider == "aml":
        verified_by = "aml"
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
            system=PRESCRIPTION_VERIFICATION_PROMPT,
            max_tokens=800,
        )
    except Exception as e:
        if provider == "aml":
            fb_provider, fb_model = verification_fallback_config()
            logger.warning("AML call failed (%s) — falling back to %s", e, fb_provider)
            provider, model = fb_provider, fb_model
            verified_by = f"{fb_provider}-fallback"
            state["sse_log"] = state.get("sse_log", []) + [
                f"[verify] AML failed — using {fb_provider} fallback"
            ]
            result = call_llm(
                provider=provider,
                model=model,
                messages=[{"role": "user", "content": user_msg}],
                system=PRESCRIPTION_VERIFICATION_PROMPT,
                max_tokens=800,
            )
        else:
            state["sse_log"] = state.get("sse_log", []) + [f"[verify] ERROR: {e}"]
            state["error"] = f"Verification failed: {e}"
            return state

    parsed = repair_json(result["text"], provider, model)

    state["prescription_verified"] = parsed.get("prescription_verified", True)
    flags = parsed.get("flags", [])
    critical_flags = [f for f in flags if f.get("severity") == "critical"]

    # Merge patient-friendly instructions back into prescribed_medications
    verified_meds = parsed.get("verified_medications", [])
    med_map = {m["name"].lower(): m for m in verified_meds}
    for med in medications:
        key = med.get("name", "").lower()
        if key in med_map and not med.get("instructions"):
            med["instructions"] = med_map[key].get("instructions", "")

    state["prescribed_medications"] = medications

    # Store flags in provenance for audit trail
    state["provenance"] = state.get("provenance", []) + [
        {
            "claim": f["description"],
            "source_id": f"prescription_verifier_{i}",
            "source_tier": "verification",
            "matched_sentence": f["description"],
            "canonical_code": None,
            "verified_by": verified_by,
        }
        for i, f in enumerate(flags)
    ]

    state["model_provider_log"] = state.get("model_provider_log", []) + [{
        "node": "prescription_verifier",
        "provider": provider,
        "model": model,
        "tokens": result.get("tokens_used", 0),
        "verified_by": verified_by,
    }]

    log = state.get("sse_log", [])
    if critical_flags:
        log.append(f"[verify] ⚠ {len(critical_flags)} critical flag(s) · verified_by={verified_by}")
    else:
        log.append(f"[verify] ✓ prescription verified · {len(flags)} warning(s) · verified_by={verified_by}")
    state["sse_log"] = log
    return state
