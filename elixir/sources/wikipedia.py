"""Wikipedia Medicine Portal — Fast mode fallback. No auth required."""
import httpx

def fetch_wikipedia(query: str, max_results: int = 3) -> list[dict]:
    snippets = []
    try:
        with httpx.Client(timeout=10, headers={"User-Agent": "elixir-research/1.0 (medical research tool)"}) as client:
            resp = client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query,
                        "format": "json", "srlimit": str(max_results)}
            )
            if resp.status_code != 200:
                return []
            results = resp.json().get("query", {}).get("search", [])
            for r in results:
                # snippet field contains html, strip tags
                import re
                clean_text = re.sub(r'<[^>]+>', '', r.get("snippet", ""))
                title = r.get("title", "")
                snippets.append({
                    "source_id": f"wikipedia:{r.get('pageid', 'unknown')}",
                    "source_name": "Wikipedia",
                    "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    "text": f"{title}. {clean_text}"[:400],
                    "entity_fingerprint": set(),
                    "source_tier": "contextual",
                })
    except Exception:
        pass
    return snippets
