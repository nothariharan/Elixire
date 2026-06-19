"""Cache utility — SHA-256 key generation for symptom caching."""
import hashlib, json


def cache_key(symptoms: list[str]) -> str:
    normalized = sorted([s.lower().strip() for s in symptoms])
    return hashlib.sha256(json.dumps(normalized).encode()).hexdigest()
