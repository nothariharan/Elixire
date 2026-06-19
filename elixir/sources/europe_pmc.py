"""Europe PMC — Primary Tier 1 medical literature source. No auth required."""
import httpx

def fetch_europe_pmc(query: str, max_results: int = 5) -> list[dict]:
    snippets = []
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={"query": query, "format": "json", "resultType": "core", "pageSize": str(max_results)}
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            for r in data.get("resultList", {}).get("result", [])[:max_results]:
                abstract = r.get("abstractText", "") or ""
                title = r.get("title", "")
                text = f"{title}. {abstract}"[:400]
                snippets.append({
                    "source_id": f"europepmc:{r.get('id', 'unknown')}",
                    "source_name": "Europe PMC",
                    "url": f"https://europepmc.org/article/MED/{r.get('id', '')}",
                    "text": text,
                    "entity_fingerprint": set(),
                    "source_tier": "literature",
                })
    except Exception:
        pass
    return snippets
