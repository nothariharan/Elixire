"""Canonical source: DailyMed API. No API key required.
Fetches FDA drug label information given an RxCUI or drug name.
"""
import httpx

DAILYMED_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"


def fetch_dailymed(query: str, max_results: int = 3) -> list[dict]:
    """Search DailyMed for FDA drug label info as canonical-tier snippets."""
    try:
        resp = httpx.get(
            f"{DAILYMED_BASE}/spls.json",
            params={"drug_name": query, "pagesize": max_results},
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        results = data.get("data", [])
        snippets = []
        for r in results[:max_results]:
            set_id = r.get("setid", "")
            title = r.get("title", query)
            snippets.append({
                "source_id": f"dailymed:{set_id}",
                "source_name": "DailyMed",
                "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}",
                "text": title[:400],
                "entity_fingerprint": set(),
                "source_tier": "canonical",
            })
        return snippets
    except Exception:
        return []
