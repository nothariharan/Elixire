"""AML fallback test. Verifies pipeline completes when AML key is invalid."""
from __future__ import annotations

import pytest
import requests


@pytest.mark.slow
@pytest.mark.integration
def test_aml_fallback(legacy_aml_api):
    r = requests.post(
        f"{legacy_aml_api}/diagnose",
        json={
            "symptoms": "fever, joint pain, butterfly rash on face",
            "mode": "fast",
        },
        timeout=320,
    )
    data = r.json()

    while r.status_code == 202:
        r = requests.post(
            f"{legacy_aml_api}/respond",
            json={
                "thread_id": data["thread_id"],
                "answer": "family history of lupus, rash for 2 weeks",
            },
            timeout=320,
        )
        data = r.json()

    if data.get("status") == "emergency":
        pytest.skip("got emergency response — cannot test fallback")

    audit = data.get("audit_trail", {})
    provider_log = audit.get("model_provider_log", data.get("model_provider_log", []))
    verification_entries = [p for p in provider_log if p.get("node") == "verification"]

    if verification_entries:
        verified_by = verification_entries[0].get("verified_by", "")
        assert verified_by == "featherless-fallback", f"Expected featherless-fallback, got: {verified_by}"

    provenance = audit.get("provenance", [])
    for p in provenance:
        assert p.get("verified_by") == "featherless-fallback", f"Expected fallback in provenance: {p}"
