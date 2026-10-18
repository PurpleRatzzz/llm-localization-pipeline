"""The only module that talks to the Claude API.

Keeping every call in one place gives three things the pipeline relies on:
- one place to change the model (the pipeline must survive model upgrades
  without rewriting prompts),
- a disk cache, so re-running an experiment never pays twice for the same request,
- exact cost accounting from the token counts the API returns.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-5-5"
# Translation needs little reasoning. At "low" effort Sonnet 5.5 skips thinking
# on most simple requests, which keeps cost and latency down.
EFFORT = "low"

# USD per million tokens, from Anthropic's price list (October 2026).
# A cache write costs 1.25x the input price; a cache read costs 0.1x.
PRICES = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
}

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "llm"

_client = None


class LLMError(Exception):
    """The model returned something the pipeline cannot use (refusal, truncation)."""


@dataclass
class LLMResponse:
    data: dict      # the parsed JSON object the model returned
    usage: dict     # token counts reported by the API
    cost_usd: float
    seconds: float  # how long the original API call took
    cached: bool    # True if served from the disk cache (no new spend)


def _get_client() -> anthropic.Anthropic:
    # Created on first use, so importing this module (e.g. in tests) and
    # re-running fully cached requests need no API key. The SDK reads
    # ANTHROPIC_API_KEY from the environment; the key is never in code.
    global _client
    if _client is None:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise LLMError("ANTHROPIC_API_KEY is not set. Set it in your shell first: "
                           "export ANTHROPIC_API_KEY=...")
        _client = anthropic.Anthropic()
    return _client


def cost_of(usage: dict, model: str) -> float:
    price = PRICES[model]
    return (
        usage["input_tokens"] * price["input"]
        + usage["output_tokens"] * price["output"]
        + usage["cache_creation_input_tokens"] * price["cache_write"]
        + usage["cache_read_input_tokens"] * price["cache_read"]
    ) / 1_000_000


def call_json(*, system: str, user: str, schema: dict, max_tokens: int = 8000,
              model: str | None = None, effort: str = EFFORT) -> LLMResponse:
    """Send one request and return the model's answer as a dict matching `schema`.

    Structured outputs (output_config.format) make the API guarantee that the
    answer is valid JSON for the schema, so we never parse free text.
    """
    # Read MODEL at call time (not as a default argument), so the CLI's
    # --model option can switch it for a whole run.
    model = model or MODEL
    params = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
    }

    # The cache key is a hash of the exact request. Change one character of a
    # prompt and the key changes, so stale answers can never be reused.
    key = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        saved = json.loads(cache_file.read_text(encoding="utf-8"))
        return LLMResponse(saved["data"], saved["usage"], cost_of(saved["usage"], model),
                           saved["seconds"], cached=True)

    start = time.perf_counter()
    try:
        response = _get_client().messages.create(**params)
    except anthropic.AuthenticationError as err:
        raise LLMError(f"The API rejected the credentials: {err.message}") from err
    seconds = time.perf_counter() - start

    # Check why the model stopped before reading the content.
    if response.stop_reason == "refusal":
        raise LLMError(f"Model refused the request: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise LLMError(f"Output cut off at max_tokens={max_tokens}; JSON would be incomplete")

    text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(text)
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,  # includes any thinking tokens
        "cache_creation_input_tokens": response.usage.cache_creation_input_tokens or 0,
        "cache_read_input_tokens": response.usage.cache_read_input_tokens or 0,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Cost and latency are stored with the answer, so a cached rerun reports
    # what producing the answer actually cost.
    saved = {"data": data, "usage": usage, "seconds": round(seconds, 2)}
    cache_file.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    return LLMResponse(data, usage, cost_of(usage, model), seconds, cached=False)
