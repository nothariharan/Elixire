"""Unpaywall — OA resolution for DOIs. Uses email-based auth."""
import httpx, os
import env_loader  # noqa: loads elixir/.env

UNPAYWALL_EMAIL = os.getenv("OPENALEX_EMAIL", "elixir@research.dev")

def fetch_unpaywall(doi: str) -> list[dict]:
    snippets = []
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"https://api.unpaywall.org/v2/{doi}",
                params={"email": UNPAYWALL_EMAIL}
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if data.get("is_oa"):
                best_oa = data.get("best_oa_location", {})
                snippets.append({
                    "source_id": f"unpaywall:{doi}",
                    "source_name": "Unpaywall",
                    "url": best_oa.get("url_for_pdf", "") or best_oa.get("url", ""),
                    "text": f"{data.get('title', '')}. Open access via {best_oa.get('host_type', 'unknown')}"[:400],
                    "entity_fingerprint": set(),
                    "source_tier": "literature",
                })
    except Exception:
        pass
    return snippets
