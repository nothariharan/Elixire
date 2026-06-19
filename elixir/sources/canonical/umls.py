"""Canonical source: UMLS Terminology Services API.
Provides ground_to_umls() for symptom→CUI mapping and fetch_umls() for research snippets.
"""
import os, re
import env_loader  # noqa
import httpx

UMLS_API_KEY = os.getenv("UMLS_API_KEY", "")
UMLS_BASE = "https://uts-ws.nlm.nih.gov/rest"


def ground_to_umls(symptoms: list[str]) -> dict[str, str]:
    """Map symptom strings to UMLS CUIs. Returns {symptom: CUI} dict.
    Falls back to empty dict if UMLS_API_KEY is not configured."""
    if not UMLS_API_KEY:
        return {}
    
    mappings = {}
    for symptom in symptoms:
        try:
            resp = httpx.get(
                f"{UMLS_BASE}/search/current",
                params={"string": symptom, "apiKey": UMLS_API_KEY, "pageSize": 1},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("result", {}).get("results", [])
                if results:
                    mappings[symptom] = results[0].get("ui", "")
        except Exception:
            continue
    return mappings


def fetch_umls(query: str, max_results: int = 5) -> list[dict]:
    """Fetch UMLS concept snippets as canonical-tier research sources."""
    if not UMLS_API_KEY:
        return []
    
    try:
        resp = httpx.get(
            f"{UMLS_BASE}/search/current",
            params={"string": query, "apiKey": UMLS_API_KEY, "pageSize": max_results},
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        results = data.get("result", {}).get("results", [])
        snippets = []
        for r in results[:max_results]:
            cui = r.get("ui", "")
            name = r.get("name", "")
            root_source = r.get("rootSource", "UMLS")
            snippets.append({
                "source_id": f"umls:{cui}",
                "source_name": "UMLS",
                "url": f"https://uts.nlm.nih.gov/uts/umls/concept/{cui}",
                "text": f"{name} (CUI: {cui}, Source: {root_source})",
                "entity_fingerprint": set(),
                "source_tier": "canonical",
            })
        return snippets
    except Exception:
        return []
