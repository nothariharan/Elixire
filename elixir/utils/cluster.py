"""Entity overlap deduplication for research snippets."""
import re

ENTITY_PATTERNS = [
    r'\b[A-Z][a-z]+(?:\s[A-Z][a-z]+){1,3}\b',
    r'\b\d{4}\b',
    r'\bPMID\s?\d+\b',
    r'\bdoi:\S+\b',
    r'\b[A-Z]{3,}\b',
]


def extract_entities(text: str) -> set[str]:
    entities = set()
    for pattern in ENTITY_PATTERNS:
        entities.update(re.findall(pattern, text))
    return {e.lower() for e in entities if len(e) > 2}


def deduplicate(snippets: list[dict], threshold: float = 0.65) -> list[dict]:
    if not snippets:
        return []

    # populate entity fingerprints
    for s in snippets:
        if not s.get("entity_fingerprint"):
            s["entity_fingerprint"] = extract_entities(s["text"])

    merged = []
    used = set()

    for i, s in enumerate(snippets):
        if i in used:
            continue
        group = [i]
        for j, t in enumerate(snippets):
            if j <= i or j in used:
                continue
            a, b = s["entity_fingerprint"], t["entity_fingerprint"]
            if not a or not b:
                continue
            overlap = len(a & b) / min(len(a), len(b))
            if overlap >= threshold:
                group.append(j)
                used.add(j)
        # keep the snippet with the most trusted source
        best = sorted(group, key=lambda idx: _source_priority(snippets[idx]["source_name"]))[0]
        merged.append(snippets[best])
        used.add(i)

    return merged


def _source_priority(name: str) -> int:
    priority = {
        "UMLS": 0, "RxNorm": 0, "DailyMed": 0,
        "Europe PMC": 1, "PubMed Central": 2, "Semantic Scholar": 3,
        "ClinicalTrials.gov": 4, "OpenAlex": 5, "Wikipedia": 6,
        "Tavily": 7, "Reddit": 8, "Brave Search": 9,
    }
    return priority.get(name, 10)
