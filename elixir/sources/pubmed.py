"""PubMed E-Utilities — Legacy Tier 1 fallback."""
import httpx, os
import env_loader  # noqa: loads elixir/.env

NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")

def fetch_pubmed(query: str, max_results: int = 5) -> list[dict]:
    snippets = []
    try:
        params = {"db": "pubmed", "term": query, "retmax": str(max_results), "retmode": "json"}
        if NCBI_API_KEY and not NCBI_API_KEY.startswith("YOUR_"):
            params["api_key"] = NCBI_API_KEY

        with httpx.Client(timeout=10, headers={"User-Agent": "elixir-research/1.0"}) as client:
            search_resp = client.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
            if search_resp.status_code != 200:
                return []
            ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                return []
            
            for pmid in ids[:max_results]:
                fetch_resp = client.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                    params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"}
                )
                if fetch_resp.status_code == 200:
                    text = fetch_resp.text[:400]
                    snippets.append({
                        "source_id": f"pubmed:{pmid}",
                        "source_name": "PubMed Central",
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        "text": text,
                        "entity_fingerprint": set(),
                        "source_tier": "literature",
                    })
    except Exception:
        pass
    return snippets
