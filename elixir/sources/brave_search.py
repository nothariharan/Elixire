"""Brave Search API — Tier 2 deep mode. Requires BRAVE_API_KEY."""
import httpx, os
import env_loader  # noqa: loads elixir/.env

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

def fetch_brave(query: str, max_results: int = 5) -> list[dict]:
    if not BRAVE_API_KEY or BRAVE_API_KEY.startswith("BSA_YOUR"):
        return []
    
    snippets = []
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query},
                headers={"X-Subscription-Token": BRAVE_API_KEY}
            )
            if resp.status_code != 200:
                return []
            results = resp.json().get("web", {}).get("results", [])
            for r in results[:max_results]:
                snippets.append({
                    "source_id": f"brave:{hash(r.get('url', '')) % 10**8}",
                    "source_name": "Brave Search",
                    "url": r.get("url", ""),
                    "text": r.get("description", "")[:400],
                    "entity_fingerprint": set(),
                    "source_tier": "contextual",
                })
    except Exception:
        pass
    return snippets
