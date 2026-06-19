"""ClinicalTrials.gov v2 — Deep mode only. No auth required."""
import httpx

def fetch_clinical_trials(query: str, max_results: int = 5) -> list[dict]:
    snippets = []
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://clinicaltrials.gov/api/v2/studies",
                params={"query.cond": query, "pageSize": str(max_results)}
            )
            if resp.status_code != 200:
                return []
            studies = resp.json().get("studies", [])
            for s in studies[:max_results]:
                proto = s.get("protocolSection", {})
                ident = proto.get("identificationModule", {})
                desc = proto.get("descriptionModule", {})
                nct_id = ident.get("nctId", "unknown")
                title = ident.get("briefTitle", "")
                summary = desc.get("briefSummary", "")
                snippets.append({
                    "source_id": f"clinicaltrials:{nct_id}",
                    "source_name": "ClinicalTrials.gov",
                    "url": f"https://clinicaltrials.gov/study/{nct_id}",
                    "text": f"{title}. {summary}"[:400],
                    "entity_fingerprint": set(),
                    "source_tier": "literature",
                })
    except Exception:
        pass
    return snippets
