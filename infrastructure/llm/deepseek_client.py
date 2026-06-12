"""DeepSeek V4 Flash API client (OpenRouter compatible).

File location: src/atlas/infrastructure/llm/deepseek_client.py

A thin async client around an OpenAI-compatible chat completions
endpoint with retry, timeout, JSON-mode support, and per-call token
telemetry.  Supports both direct DeepSeek and OpenRouter via config.

Two callers in the pipeline:
- intent_classifier.py       → text mode, short answer
- filter_extractor.py        → JSON mode, strict schema
- pipeline.py final answer   → text mode, longer answer

This is the only place that talks to the LLM API. Swapping
providers means changing this one file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    """Return the best available API key.

    Reads from environment at call time so the caller can set
    the env var before importing the module.  Precedence:
    DEEPSEEK_API_KEY > OPENROUTER_API_KEY.
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise DeepSeekConfigError(
            "No API key configured. Set DEEPSEEK_API_KEY or OPENROUTER_API_KEY "
            "in your .env file or environment."
        )
    return key


def _get_model() -> str:
    """Return the model name based on the configured provider."""
    dk = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if dk:
        return os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

    # Using OpenRouter
    ork = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ork:
        return os.environ.get("DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")

    return os.environ.get("DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")


def _get_api_base() -> str:
    """Return the API base URL based on the configured provider."""
    explicit = os.environ.get("DEEPSEEK_API_BASE")
    if explicit:
        return explicit

    dk = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if dk:
        return "https://api.deepseek.com"

    ork = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ork:
        return "https://openrouter.ai/api/v1"

    return "https://api.deepseek.com"


def _using_openrouter() -> bool:
    """Return True if routing through OpenRouter (no DEEPSEEK_API_KEY)."""
    dk = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    ork = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return bool(ork) and not bool(dk)

_DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("DEEPSEEK_TIMEOUT", "30"))
_MAX_RETRIES = int(os.environ.get("DEEPSEEK_MAX_RETRIES", "2"))

# Retryable HTTP status codes (rate limit + transient server errors).
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


# ── Types ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeepSeekResponse:
    """The bits of the response the pipeline cares about."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str


class DeepSeekError(RuntimeError):
    """Raised when the API call fails after all retries.

    The pipeline catches this and falls back to deterministic behavior.
    """


class DeepSeekConfigError(RuntimeError):
    """Raised when no API key is configured."""


# ── Client ────────────────────────────────────────────────────────────────────


async def call_deepseek(
    *,
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int = 1000,
    timeout_seconds: float | None = None,
) -> DeepSeekResponse:
    """Call the LLM with retry + backoff.

    Parameters
    ----------
    prompt
        The user-role message.
    system
        Optional system-role message. The pipeline embeds grounding
        instructions in `prompt` rather than `system` for the final
        answer call so the rules travel with the evidence atomically.
    json_mode
        When True, requests `response_format={"type": "json_object"}`.
        The LLM is then constrained to return valid JSON. Used by
        Stage 1 (intent) and Stage 2 (filter extraction).
    temperature
        0.0 for extraction (deterministic), 0.1 for answer generation.
    max_tokens
        Cap on response size. Used to control cost.

    Raises
    ------
    DeepSeekConfigError
        If no API key is configured.
    DeepSeekError
        If all retries fail. Caller should fall back gracefully.
    """
    api_key = _get_api_key()
    timeout = timeout_seconds or _DEFAULT_TIMEOUT_SECONDS

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: dict[str, Any] = {
        "model": _get_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # OpenRouter needs extra headers for OAuth routing
    if _using_openrouter():
        headers["HTTP-Referer"] = "https://atlas.nousresearch.com"
        headers["X-Title"] = "Atlas Aviation Accident Platform"

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await client.post(
                    f"{_get_api_base()}/chat/completions",
                    headers=headers,
                    json=body,
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning("DeepSeek timeout on attempt %d: %s", attempt + 1, exc)
                await _backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("DeepSeek HTTP error on attempt %d: %s", attempt + 1, exc)
                await _backoff(attempt)
                continue

            if response.status_code == 200:
                return _parse_response(response.json())

            if response.status_code in _RETRYABLE_STATUSES:
                last_error = DeepSeekError(
                    f"DeepSeek returned {response.status_code}: {response.text[:200]}"
                )
                logger.warning(
                    "DeepSeek retryable status %d on attempt %d", response.status_code, attempt + 1
                )
                await _backoff(attempt, response.headers.get("retry-after"))
                continue

            # Non-retryable error — give up immediately.
            raise DeepSeekError(
                f"DeepSeek error {response.status_code}: {response.text[:200]}"
            )

    raise DeepSeekError(
        f"DeepSeek call failed after {_MAX_RETRIES + 1} attempts: {last_error}"
    )


def _parse_response(data: dict[str, Any]) -> DeepSeekResponse:
    """Extract the bits we care about from the OpenAI-compatible response."""
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"] or ""
        finish_reason = choice.get("finish_reason", "unknown")
        usage = data.get("usage", {})
        return DeepSeekResponse(
            content=content,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            finish_reason=finish_reason,
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError(f"Malformed DeepSeek response: {exc}") from exc


async def _backoff(attempt: int, retry_after_header: str | None = None) -> None:
    """Sleep with exponential backoff or Retry-After header value."""
    if retry_after_header:
        try:
            await asyncio.sleep(min(float(retry_after_header), 10.0))
            return
        except ValueError:
            pass
    # 0.5, 1.0, 2.0 seconds with jitter
    base = 0.5 * (2**attempt)
    await asyncio.sleep(base)


# ── JSON-mode helpers ─────────────────────────────────────────────────────────


def parse_json_or_none(content: str) -> dict[str, Any] | None:
    """Best-effort JSON parse with code-fence stripping.

    The LLM sometimes wraps JSON in ```json ... ``` blocks even when
    instructed not to. Strip those before parsing. Returns None on
    failure rather than raising — the caller decides whether to
    fall back to the deterministic parser.
    """
    text = content.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse DeepSeek JSON response: %s", exc)
    return None


__all__ = [
    "DeepSeekConfigError",
    "DeepSeekError",
    "DeepSeekResponse",
    "call_deepseek",
    "parse_json_or_none",
]
