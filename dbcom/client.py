"""Multi-turn chat client with strict JSON-schema output, 429 backoff and per-model call stats.

Provider routing: gateway names ("azure_ai/...", "vertex_ai/...", "mistral/...") go to the Mistral
internal LiteLLM gateway with LITELLM_API_KEY; Mistral-family names (mistral-*, ministral-*, ...) go to
the Mistral API with MISTRAL_API_KEY; other names with a vendor prefix (e.g. "openai/gpt-5.6") go to
OpenRouter with OPENROUTER_API_KEY.

A model spec may end with "@<effort>" (e.g. "mistral/zai-glm-5-2@high"), which sends
reasoning_effort=<effort>. Stats are keyed by the full spec.
"""
import logging
import os
import random
import threading
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv("LITELLM_BASE_URL", "https://internal-ai-gateway.mistralai.com/v1")
GATEWAY_PREFIXES = ("azure_ai", "vertex_ai", "mistral")  # Mistral internal LiteLLM gateway (see GET /v1/models)
MAX_TOKENS = 16000
TIMEOUT = 300
TRANSPORT_RETRIES = 10  # 429 / 5xx / timeouts, before the call counts as one failed attempt

_stats_lock = threading.Lock()
_stats = defaultdict(lambda: defaultdict(int))


def _bump(model, key, n=1):
    with _stats_lock:
        _stats[model][key] += n


def stats_summary():
    with _stats_lock:
        return "; ".join(
            f"{m}: calls={s['calls']} 429s={s['rate_limited']} failed={s['failed']} "
            f"failed_rate_limit={s['failed_rate_limit']} tokens_in={s['prompt_tokens']} tokens_out={s['completion_tokens']}"
            for m, s in sorted(_stats.items()))


def parse_spec(spec):
    """'name@effort' -> (name, effort); 'name' -> (name, None)."""
    name, _, effort = spec.partition("@")
    return name, effort or None


def provider_for(model):
    model = parse_spec(model)[0]
    if model.split("/")[0] in GATEWAY_PREFIXES:
        return GATEWAY_URL, os.getenv("LITELLM_API_KEY", "")
    if "/" in model:
        return "https://openrouter.ai/api/v1", os.getenv("OPENROUTER_API_KEY", "")
    if model.split("-")[0] in ("mistral", "ministral", "magistral", "codestral", "devstral"):
        return "https://api.mistral.ai/v1", os.getenv("MISTRAL_API_KEY", "")
    raise ValueError(f"don't know which provider serves {model!r}")


class BackendError(Exception):
    def __init__(self, msg, rate_limited=False):
        super().__init__(msg)
        self.rate_limited = rate_limited


def _split_content(message):
    """Return (text, thinking). The Mistral API returns reasoning as "thinking" content blocks;
    the LiteLLM gateway returns it in message.reasoning_content."""
    content = message.get("content")
    text, thinking = [], [message.get("reasoning_content") or ""]
    if isinstance(content, str) or content is None:
        text.append(content or "")
    else:
        for b in content:
            if b.get("type") == "text":
                text.append(b.get("text", ""))
            elif b.get("type") == "thinking":
                thinking.extend(t.get("text", "") for t in b.get("thinking", []) if isinstance(t, dict))
    return "".join(text), "\n".join(t for t in thinking if t)


def chat(model, messages, schema_name, schema):
    """One completion. Retries transport errors with exponential backoff.
    Returns dict(text, thinking, usage, finish_reason, duration_s, http429_retries). Raises BackendError."""
    base_url, key = provider_for(model)
    name, effort = parse_spec(model)
    payload = {
        "model": name,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
    }
    if effort:
        payload["reasoning_effort"] = effort
    _bump(model, "calls")
    retries_429, last = 0, None
    t0 = time.time()
    for attempt in range(1, TRANSPORT_RETRIES + 1):
        retry_after = None
        try:
            r = requests.post(f"{base_url}/chat/completions", json=payload, timeout=TIMEOUT,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            if r.status_code == 429 or r.status_code >= 500:
                if r.status_code == 429:
                    retries_429 += 1
                    _bump(model, "rate_limited")
                last = BackendError(f"HTTP {r.status_code}: {r.text[:200]}", rate_limited=r.status_code == 429)
                retry_after = r.headers.get("Retry-After")
            elif r.status_code != 200:
                # 4xx other than 429: the request itself is wrong, retrying won't help
                _bump(model, "failed")
                raise BackendError(f"HTTP {r.status_code}: {r.text[:500]}")
            else:
                d = r.json()
                choice = d["choices"][0]
                text, thinking = _split_content(choice["message"])
                usage = d.get("usage") or {}
                _bump(model, "prompt_tokens", usage.get("prompt_tokens", 0))
                _bump(model, "completion_tokens", usage.get("completion_tokens", 0))
                return {"text": text, "thinking": thinking, "usage": usage,
                        "finish_reason": choice.get("finish_reason"),
                        "duration_s": round(time.time() - t0, 1), "http429_retries": retries_429}
        except (requests.Timeout, requests.ConnectionError) as e:
            last = BackendError(f"{type(e).__name__}: {e}")
        if attempt < TRANSPORT_RETRIES:
            # 5, 10, 20, 40, 60, 60... seconds with jitter so parallel players don't retry in lockstep
            delay = min(60, 5 * 2 ** (attempt - 1)) + random.uniform(0, 3)
            try:
                delay = max(delay, float(retry_after)) if retry_after else delay
            except ValueError:
                pass
            time.sleep(delay)
    _bump(model, "failed")
    if last is not None and last.rate_limited:
        _bump(model, "failed_rate_limit")
    logger.error(f"LLM_CALL_FAILED model={model} after {TRANSPORT_RETRIES} transport attempts: {last} | {stats_summary()}")
    raise last
