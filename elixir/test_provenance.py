"""Provenance completeness test. Requires Featherless + optional AML keys."""
from __future__ import annotations

import pytest
import requests


def _run_full_pipeline(base: str) -> dict:
    r = requests.post(
        f"{base}/diagnose",
        json={
            "symptoms": "fever, joint pain, butterfly rash on face",
            "mode": "fast",
        },
        timeout=320,
    )
    data = r.json()

    while r.status_code == 202:
        r = requests.post(
            f"{base}/respond",
            json={
                "thread_id": data["thread_id"],
                "answer": "family history of lupus, rash for 2 weeks, mouth sores, sun sensitivity",
            },
            timeout=320,
        )
        data = r.json()

    return data


@pytest.mark.slow
@pytest.mark.integration
def test_provenance_completeness(legacy_api):
    data = _run_full_pipeline(legacy_api)

    if data.get("status") == "emergency":
        pytest.skip("got emergency response — skipping provenance test")

    audit = data.get("audit_trail", {})
    provenance = audit.get("provenance", [])

    contextual_provenance = [p for p in provenance if p.get("source_tier") == "contextual"]
    assert len(contextual_provenance) == 0, f"Contextual sources in provenance: {contextual_provenance}"

    tier_breakdown = audit.get("source_tier_breakdown", {})
    assert isinstance(tier_breakdown, dict)
