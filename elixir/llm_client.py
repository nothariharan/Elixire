"""Unified LLM client — Featherless, AML (OpenAI-compatible), or AWS Bedrock Nova."""
import json, os, logging
import env_loader  # noqa: loads elixir/.env
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("elixir.llm")

FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY", "")
FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"
FEATHERLESS_MODEL_TRIAGE = os.getenv("FEATHERLESS_MODEL_TRIAGE", "")
FEATHERLESS_MODEL_ACTION = os.getenv("FEATHERLESS_MODEL_ACTION", "")

AML_API_KEY = os.getenv("AML_API_KEY", "")
AML_BASE_URL = os.getenv("AML_BASE_URL", "https://api.aimlapi.com/v1")
AML_MODEL_VERIFICATION = os.getenv("AML_MODEL_VERIFICATION", "")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "featherless").lower()
ORCHESTRATOR_MODEL_ID = os.getenv("ORCHESTRATOR_MODEL_ID", "amazon.nova-lite-v1:0")
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))

_clients: dict[str, OpenAI] = {}
_bedrock_client = None


def aws_configured() -> bool:
    return bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))


def llm_config_for(node: str) -> tuple[str, str]:
    """Return (provider, model) for a pipeline node."""
    if LLM_PROVIDER == "bedrock":
        if not aws_configured():
            raise RuntimeError("LLM_PROVIDER=bedrock but AWS credentials missing")
        return "bedrock", ORCHESTRATOR_MODEL_ID
    if node == "action":
        return "featherless", FEATHERLESS_MODEL_ACTION
    if node == "verification" and AML_MODEL_VERIFICATION:
        return "aml", AML_MODEL_VERIFICATION
    return "featherless", FEATHERLESS_MODEL_TRIAGE


def verification_fallback_config() -> tuple[str, str]:
    """Provider/model when AML is unavailable."""
    if LLM_PROVIDER == "bedrock" and aws_configured():
        return "bedrock", ORCHESTRATOR_MODEL_ID
    return "featherless", FEATHERLESS_MODEL_TRIAGE


def _get_client(provider: str) -> OpenAI:
    if provider not in _clients:
        if provider == "featherless":
            if not FEATHERLESS_API_KEY:
                raise RuntimeError("FEATHERLESS_API_KEY not configured")
            _clients[provider] = OpenAI(
                base_url=FEATHERLESS_BASE_URL,
                api_key=FEATHERLESS_API_KEY,
            )
        elif provider == "aml":
            if not AML_API_KEY:
                raise RuntimeError("AML_API_KEY not configured")
            _clients[provider] = OpenAI(
                base_url=AML_BASE_URL,
                api_key=AML_API_KEY,
            )
        else:
            raise ValueError(f"Unknown OpenAI-compatible provider: {provider}")
    return _clients[provider]


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3

        if not aws_configured():
            raise RuntimeError("AWS credentials not configured")
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        )
    return _bedrock_client


def _call_bedrock(
    model: str,
    messages: list[dict],
    system: str,
    temperature: float,
    max_tokens: int,
) -> dict:
    client = _get_bedrock_client()
    converse_messages = [
        {
            "role": m["role"],
            "content": [{"text": m["content"]}],
        }
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]
    response = client.converse(
        modelId=model,
        system=[{"text": system}],
        messages=converse_messages,
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": temperature,
        },
    )
    output = response.get("output", {}).get("message", {})
    parts = output.get("content") or []
    text = parts[0].get("text", "") if parts else ""
    usage = response.get("usage") or {}
    tokens = usage.get("totalTokens") or usage.get("inputTokens", 0) + usage.get("outputTokens", 0)
    logger.info(f"[bedrock] {model} — {tokens} tokens")
    return {
        "text": text,
        "tokens_used": tokens,
        "provider": "bedrock",
        "model": model,
    }


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4))
def call_llm(
    provider: str,
    model: str,
    messages: list[dict],
    system: str,
    temperature: float = 0.0,
    max_tokens: int = 1200,
) -> dict:
    if provider == "bedrock":
        return _call_bedrock(model, messages, system, temperature, max_tokens)

    client = _get_client(provider)
    full_messages = [{"role": "system", "content": system}] + messages
    response = client.chat.completions.create(
        model=model,
        messages=full_messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = response.choices[0].message.content or ""
    tokens = response.usage.total_tokens if response.usage else 0
    logger.info(f"[{provider}] {model} — {tokens} tokens")
    return {
        "text": text,
        "tokens_used": tokens,
        "provider": provider,
        "model": model,
    }


def repair_json(raw: str, provider: str, model: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        repair_result = call_llm(
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": f"Fix this malformed JSON and return ONLY valid JSON:\n{raw}"}],
            system="Return only valid JSON. No explanation, no markdown fences.",
        )
        fixed = repair_result["text"].strip()
        if fixed.startswith("```"):
            lines = fixed.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            fixed = "\n".join(lines)
        return json.loads(fixed)
