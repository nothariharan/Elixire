"""Canonical source: RxNorm API. No API key required.
Normalizes medication names to RxCUI codes.
"""
import httpx

RXNORM_BASE = "https://rxnav.nlm.nih.gov/REST"


def normalize_medication(med_name: str) -> dict | None:
    """Given a medication name, return {name, rxcui} or None."""
    try:
        resp = httpx.get(
            f"{RXNORM_BASE}/rxcui.json",
            params={"name": med_name},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        ids = data.get("idGroup", {}).get("rxnormId", [])
        if ids:
            return {"name": med_name, "rxcui": ids[0]}
        return None
    except Exception:
        return None


def fetch_rxnorm(query: str, max_results: int = 3) -> list[dict]:
    """Search RxNorm for drug information as canonical-tier snippets."""
    try:
        resp = httpx.get(
            f"{RXNORM_BASE}/drugs.json",
            params={"name": query},
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        groups = data.get("drugGroup", {}).get("conceptGroup", [])
        snippets = []
        for group in groups:
            for prop in group.get("conceptProperties", [])[:max_results]:
                rxcui = prop.get("rxcui", "")
                name = prop.get("name", "")
                tty = prop.get("tty", "")
                snippets.append({
                    "source_id": f"rxnorm:{rxcui}",
                    "source_name": "RxNorm",
                    "url": f"https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm={rxcui}",
                    "text": f"{name} (RxCUI: {rxcui}, Type: {tty})",
                    "entity_fingerprint": set(),
                    "source_tier": "canonical",
                })
            if len(snippets) >= max_results:
                break
        return snippets[:max_results]
    except Exception:
        return []
