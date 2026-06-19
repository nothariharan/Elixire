"""Semantic Scholar Graph API — Deep mode. Optional API key."""
import httpx, os
import env_loader  # noqa: loads elixir/.env

SEMANTIC_SCHOLAR_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")

def fetch_semantic_scholar(query: str, max_results: int = 5) -> list[dict]:
    snippets = []
    try:
        headers = {}
        if SEMANTIC_SCHOLAR_KEY and not SEMANTIC_SCHOLAR_KEY.startswith("YOUR_"):
            headers["x-api-key"] = SEMANTIC_SCHOLAR_KEY

        with httpx.Client(timeout=10, headers=headers) as client:
            resp = client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": query, "fields": "title,abstract,year,externalIds", "limit": str(max_results)}
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            for p in data.get("data", [])[:max_results]:
                title = p.get("title", "")
                abstract = p.get("abstract", "") or ""
                paper_id = p.get("paperId", "unknown")
                snippets.append({
                    "source_id": f"semanticscholar:{paper_id}",
                    "source_name": "Semantic Scholar",
                    "url": f"https://www.semanticscholar.org/paper/{paper_id}",
                    "text": f"{title}. {abstract}"[:400],
                    "entity_fingerprint": set(),
                    "source_tier": "literature",
                })
    except Exception:
        pass
    return snippets
