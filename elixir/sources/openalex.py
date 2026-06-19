"""OpenAlex API — Deep mode. Free with email polite pool."""
import httpx, os
import env_loader  # noqa: loads elixir/.env

OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "")

def fetch_openalex(query: str, max_results: int = 5) -> list[dict]:
    snippets = []
    try:
        params = {"search": query, "per_page": str(max_results)}
        if OPENALEX_EMAIL:
            params["mailto"] = OPENALEX_EMAIL

        with httpx.Client(timeout=10) as client:
            resp = client.get("https://api.openalex.org/works", params=params)
            if resp.status_code != 200:
                return []
            results = resp.json().get("results", [])
            for r in results[:max_results]:
                title = r.get("title", "") or ""
                # openalex stores abstract as inverted index, reconstruct it
                abstract_inv = r.get("abstract_inverted_index", {})
                if abstract_inv:
                    word_positions = []
                    for word, positions in abstract_inv.items():
                        for pos in positions:
                            word_positions.append((pos, word))
                    word_positions.sort()
                    abstract = " ".join(w for _, w in word_positions)
                else:
                    abstract = ""

                oa_id = r.get("id", "unknown").split("/")[-1]
                snippets.append({
                    "source_id": f"openalex:{oa_id}",
                    "source_name": "OpenAlex",
                    "url": r.get("doi", "") or r.get("id", ""),
                    "text": f"{title}. {abstract}"[:400],
                    "entity_fingerprint": set(),
                    "source_tier": "literature",
                })
    except Exception:
        pass
    return snippets
