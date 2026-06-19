"""Tavily Search API — Tier 2 deep mode web search."""
import os
import env_loader  # noqa: loads elixir/.env

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# allowed domains from sources.json
ALLOWED_DOMAINS = [
    "nih.gov", "pubmed.ncbi.nlm.nih.gov", "mayoclinic.org",
    "who.int", "nejm.org", "thelancet.com", "bmj.com",
]

def fetch_tavily(query: str, max_results: int = 5) -> list[dict]:
    if not TAVILY_API_KEY or TAVILY_API_KEY.startswith("tvly-YOUR"):
        return []
    
    snippets = []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_domains=ALLOWED_DOMAINS,
        )
        for r in results.get("results", [])[:max_results]:
            snippets.append({
                "source_id": f"tavily:{hash(r.get('url', '')) % 10**8}",
                "source_name": "Tavily",
                "url": r.get("url", ""),
                "text": r.get("content", "")[:400],
                "entity_fingerprint": set(),
                "source_tier": "contextual",
            })
    except Exception:
        pass
    return snippets
