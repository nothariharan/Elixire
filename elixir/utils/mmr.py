"""Maximal Marginal Relevance (MMR) selection for snippet diversity."""
import re
from math import log


def _tfidf_vector(text: str, corpus: list[str]) -> dict[str, float]:
    tokens = re.findall(r'\b\w+\b', text.lower())
    if not tokens:
        return {}
    tf = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    n = len(corpus)
    df = {t: sum(1 for doc in corpus if t in doc.lower()) for t in tf}
    return {
        t: (freq / len(tokens)) * log((n + 1) / (df.get(t, 0) + 1))
        for t, freq in tf.items()
    }


def _cosine(a: dict, b: dict) -> float:
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    dot = sum(a[k] * b[k] for k in keys)
    mag_a = sum(v ** 2 for v in a.values()) ** 0.5
    mag_b = sum(v ** 2 for v in b.values()) ** 0.5
    return dot / (mag_a * mag_b + 1e-9)


def mmr_select(snippets: list[dict], k: int = 10, lambda_: float = 0.5) -> list[dict]:
    if len(snippets) <= k:
        return snippets

    corpus = [s["text"] for s in snippets]
    query_vec = _tfidf_vector(" ".join(corpus[:3]), corpus)
    vecs = [_tfidf_vector(s["text"], corpus) for s in snippets]

    selected_idx = []
    remaining = list(range(len(snippets)))

    for _ in range(k):
        if not remaining:
            break
        scores = []
        for i in remaining:
            relevance = _cosine(query_vec, vecs[i])
            if not selected_idx:
                redundancy = 0.0
            else:
                redundancy = max(_cosine(vecs[i], vecs[j]) for j in selected_idx)
            scores.append((lambda_ * relevance - (1 - lambda_) * redundancy, i))
        best = max(scores, key=lambda x: x[0])[1]
        selected_idx.append(best)
        remaining.remove(best)

    return [snippets[i] for i in selected_idx]
