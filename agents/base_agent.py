"""
Base Agent class with multi-LLM fallback chain.

Default fallback order (auto-detected from .env API keys):
  1. GLM-4.7 via Z.AI   (primary — Anthropic-compatible, cheapest)
  2. DeepSeek-V3         (fallback — if DEEPSEEK_API_KEY set)
  3. Ollama local        (air-gap / last resort)
Override with PRIMARY_MODEL env var.
"""

import asyncio
import json
import logging
import os
import time
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Optional

import anthropic
import httpx
from openai import AsyncOpenAI

from config import settings
from observability import tracer as _tracer
from services.llm_logger import (
    canonical_prompt as _canonical_prompt,
    current_run_id as _current_run_id,
    log_llm_call as _log_llm_call,
    now_ms as _now_ms,
)

_otel_tracer = _tracer("hardware-pipeline.agent")

logger = logging.getLogger(__name__)


# P26 (2026-04-25) — 429 rate-limit retry with exponential backoff.
#
# Symptom this fixes (project djd, P7 failure):
#   "All models in fallback chain failed. Last error:
#    Error code: 429 - {'error': {'code': '1302',
#    'message': 'Rate limit reached for requests'}}"
#
# When the DAG fires P3+P6+P8a+P7+P8b+P8c in parallel and HRS internally
# fires 8 sub-section calls in parallel, GLM/Z.AI's rate limiter throws
# 429s. The previous code immediately fell through to the next model in
# the chain — but with both glm-5.1 and glm-4.7 on the SAME Z.AI account,
# both hit the rate limit at the same moment and the chain exhausted.
#
# New behaviour: on 429 (or other transient errors — 5xx, network), sleep
# with exponential backoff and retry the SAME model up to N times BEFORE
# falling through to the next model. Permanent errors (401, 402, 404) still
# fall through immediately so we don't waste time retrying e.g. a missing
# DeepSeek balance.
_RETRY_MAX_ATTEMPTS_PER_MODEL = 3
_RETRY_BACKOFF_BASE_S = 5.0   # 1st retry waits 5s, 2nd waits 10s, 3rd waits 20s

# When a model exhausts its same-model retries with transient errors and we
# fall through to the next provider, pause briefly first. Without this, a
# multi-provider 429 storm exhausts the entire fallback chain in <1s and the
# user sees "All models failed" with no actual retry having happened. The
# backoff is small (3s) because individual providers already retried 3x.
_INTER_PROVIDER_BACKOFF_S = 3.0
_RETRY_BACKOFF_FACTOR = 2.0


def _is_transient_error(exc: BaseException) -> bool:
    """Should we sleep + retry the SAME model, or fall through to the next?

    Transient (retry):  429 rate-limit, 5xx, connection-reset, timeout.
    Permanent (skip):   401 auth, 402 insufficient balance, 404 model-not-found,
                        400 bad-request (these won't change on retry).
    """
    # Anthropic SDK rate-limit (429) — always transient.
    if isinstance(exc, anthropic.RateLimitError):
        return True
    # Anthropic SDK 5xx → server error → transient.
    if isinstance(exc, anthropic.APIStatusError):
        sc = getattr(exc, "status_code", None)
        if sc is not None:
            if sc == 429 or 500 <= sc <= 599:
                return True
            return False  # 4xx other than 429 = permanent
        # No status_code attr — fall back to message inspection.
    # OpenAI SDK (DeepSeek path) — has its own RateLimitError + APIStatusError.
    try:
        import openai as _openai
        if isinstance(exc, _openai.RateLimitError):
            return True
        if isinstance(exc, _openai.APIStatusError):
            sc = getattr(exc, "status_code", None)
            if sc is not None:
                if sc == 429 or 500 <= sc <= 599:
                    return True
                return False
    except Exception:
        pass
    # httpx network / timeout errors — transient.
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    # Last-resort string sniff for "rate limit" / "429" in the error message,
    # since httpx-wrapped errors can lose the original status code.
    s = str(exc).lower()
    if "rate limit" in s or "429" in s:
        return True
    if "503" in s or "502" in s or "504" in s or "service unavailable" in s:
        return True
    return False


def _get_proxy() -> Optional[str]:
    """
    Detect proxy for outbound HTTPS — in order of priority:
      1. HTTPS_PROXY / HTTP_PROXY env var (set in .env)
      2. Windows system proxy (reads IE/WinHTTP settings via urllib)
    Returns proxy URL string or None.
    """
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if proxy:
        return proxy
    # Auto-detect Windows system proxy
    try:
        sys_proxies = urllib.request.getproxies()
        proxy = sys_proxies.get("https") or sys_proxies.get("http")
        if proxy:
            logger.info("base_agent.proxy_autodetected: %s", proxy)
            return proxy
    except Exception:
        pass
    return None


def _is_no_proxy(host: str) -> bool:
    """Check if a hostname should bypass the proxy (NO_PROXY env var)."""
    no_proxy = (
        os.environ.get("NO_PROXY")
        or os.environ.get("no_proxy")
        or ""
    )
    if not no_proxy:
        return False
    domains = {d.strip().lstrip(".").lower() for d in no_proxy.split(",") if d.strip()}
    host_lower = host.lower()
    return any(host_lower == d or host_lower.endswith("." + d) for d in domains)


_MITM_CA_PATHS = [
    "/etc/ssl/certs/mitm-proxy-ca.pem",   # Cowork sandbox
    "/usr/local/share/ca-certificates/mitm-proxy-ca.crt",
]


def _get_ssl_verify() -> str | bool:
    """Return CA bundle path for the sandbox proxy, or True for default system certs."""
    # Allow override via env var
    ca_bundle = os.environ.get("SSL_CA_BUNDLE", "")
    if ca_bundle and os.path.exists(ca_bundle):
        return ca_bundle
    # Auto-detect MITM proxy CA (present in Cowork sandbox)
    for path in _MITM_CA_PATHS:
        if os.path.exists(path):
            return path
    return True  # Use system/default cert store


def _make_sync_httpx_client(target_host: Optional[str] = None) -> Optional[httpx.Client]:
    proxy = _get_proxy()
    if not proxy:
        return None
    if target_host and _is_no_proxy(target_host):
        logger.info("base_agent.direct_connection (no_proxy bypass): %s", target_host)
        return None  # Direct connection — no proxy
    verify = _get_ssl_verify()
    logger.info("base_agent.using_proxy (sync): %s -> %s (verify=%s)", target_host or "?", proxy, verify)
    return httpx.Client(proxy=proxy, verify=verify, timeout=120.0)


def _make_async_httpx_client(target_host: Optional[str] = None) -> Optional[httpx.AsyncClient]:
    proxy = _get_proxy()
    if not proxy:
        return None
    if target_host and _is_no_proxy(target_host):
        logger.info("base_agent.direct_connection (no_proxy bypass): %s", target_host)
        return None  # Direct connection — no proxy
    verify = _get_ssl_verify()
    logger.info("base_agent.using_proxy (async): %s -> %s (verify=%s)", target_host or "?", proxy, verify)
    return httpx.AsyncClient(proxy=proxy, verify=verify, timeout=120.0)


class BaseAgent(ABC):
    """
    Abstract base for all pipeline phase agents.

    Each agent has:
    - A system prompt with domain expertise
    - Access to specific tools (Claude tool_use format)
    - Fallback chain for token limits / rate limits
    - Structured input/output
    """

    def __init__(
        self,
        phase_number: str,
        phase_name: str,
        model: Optional[str] = None,
        system_prompt: str = "",
        tools: Optional[list[dict]] = None,
        max_tokens: int = 16384,
    ):
        self.phase_number = phase_number
        self.phase_name = phase_name
        self.model = model or settings.primary_model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.max_tokens = max_tokens

        # Initialize Anthropic client (Claude API)
        self._anthropic_client: Optional[anthropic.Anthropic] = None
        if settings.anthropic_api_key:
            _hc = _make_sync_httpx_client("api.anthropic.com")
            self._anthropic_client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key,
                **( {"http_client": _hc} if _hc else {} ),
            )

        # Initialize DeepSeek client (OpenAI-compatible API)
        self._deepseek_client: Optional[AsyncOpenAI] = None
        if settings.deepseek_api_key:
            _ahc = _make_async_httpx_client("api.deepseek.com")
            self._deepseek_client = AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                **( {"http_client": _ahc} if _ahc else {} ),
            )
            logger.info("DeepSeek client initialized — available as fallback LLM")

        # Fallback chain — auto-promote based on available keys
        self.fallback_chain = settings.fallback_chain
        logger.info(
            "agent.init phase=%s primary_model=%s chain=%s",
            phase_number, self.model, self.fallback_chain,
            extra={"phase": phase_number, "model": self.model},
        )
        if not settings.anthropic_api_key and not settings.deepseek_api_key and settings.glm_api_key:
            logger.info("GLM via Z.AI is primary LLM (no Anthropic/DeepSeek key set)")

    @abstractmethod
    async def execute(self, project_context: dict, user_input: str) -> dict:
        """
        Execute this agent's phase.

        Args:
            project_context: Current project state and outputs from prior phases.
            user_input: User's message or requirements text.

        Returns:
            dict with phase outputs (files generated, data extracted, etc.)
        """
        pass

    @abstractmethod
    def get_system_prompt(self, project_context: dict) -> str:
        """Build the system prompt with project-specific context."""
        pass

    async def call_llm(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        model: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: Optional[int] = None,
        tool_choice: Optional[dict] = None,
    ) -> dict:
        """
        Call Claude API with automatic fallback chain.

        Returns:
            dict with 'content' (text), 'tool_calls' (if any), 'model_used', 'stop_reason'
        """
        model = model or self.model
        system = system or self.system_prompt
        tools = tools or self.tools
        max_tokens = max_tokens or self.max_tokens

        # Try each model in the fallback chain
        chain = [model] + [m for m in self.fallback_chain if m != model]

        # One parent span per call_llm — captures the whole fallback traversal.
        # Child _call_model calls show up as attributes on this parent so we
        # can see which model actually answered.
        with _otel_tracer.start_as_current_span(f"llm.{self.phase_number}") as span:
            span.set_attribute("llm.phase", self.phase_number or "")
            span.set_attribute("llm.model_requested", model)
            span.set_attribute("llm.message_count", len(messages))
            span.set_attribute("llm.tool_count", len(tools) if tools else 0)
            span.set_attribute("llm.max_tokens", max_tokens)
            last_error = None
            for fallback_model in chain:
                # P26 (2026-04-25): per-model retry loop. On 429 / transient
                # error, sleep with exponential backoff and retry the SAME
                # model up to _RETRY_MAX_ATTEMPTS_PER_MODEL times BEFORE
                # falling through to the next model in the chain. See the
                # `_is_transient_error` docstring for the transient/permanent
                # split — permanent errors (401/402/404) skip retry and fall
                # through immediately.
                attempt_in_model = 0
                while True:
                    attempt_in_model += 1
                    _start_ms = _now_ms()
                    try:
                        result = await self._call_model(
                            fallback_model, messages, system, tools, max_tokens, tool_choice
                        )
                        if not result:
                            # `_call_model` returned None (unknown model type
                            # or routing failure). NOT transient — no point
                            # retrying. Fall through to next model.
                            logger.warning(
                                "llm.unknown_model phase=%s model=%s — _call_model "
                                "returned None, falling through",
                                self.phase_number, fallback_model,
                                extra={"phase": self.phase_number},
                            )
                            last_error = RuntimeError(
                                f"_call_model returned None for {fallback_model!r}"
                            )
                            break  # next model in chain
                        result["model_used"] = fallback_model
                        usage = result.get("usage", {})
                        logger.info(
                            "llm.call_ok phase=%s model=%s in=%s out=%s attempts=%d",
                            self.phase_number, fallback_model,
                            usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                            attempt_in_model,
                            extra={"phase": self.phase_number, "model": fallback_model},
                        )
                        span.set_attribute("llm.model_used", fallback_model)
                        span.set_attribute("llm.tokens_in",  usage.get("input_tokens", 0) or 0)
                        span.set_attribute("llm.tokens_out", usage.get("output_tokens", 0) or 0)
                        span.set_attribute("llm.stop_reason", result.get("stop_reason", "") or "")
                        span.set_attribute("llm.latency_ms", _now_ms() - _start_ms)
                        span.set_attribute("llm.tool_calls", len(result.get("tool_calls") or []))
                        span.set_attribute("llm.attempts", attempt_in_model)
                        # B1.3 — persist one llm_calls row per successful call.
                        try:
                            _log_llm_call(
                                pipeline_run_id=_current_run_id(),
                                model=fallback_model,
                                prompt=_canonical_prompt(messages, system),
                                response=result.get("content", ""),
                                tokens_in=usage.get("input_tokens"),
                                tokens_out=usage.get("output_tokens"),
                                latency_ms=_now_ms() - _start_ms,
                                tool_calls=result.get("tool_calls") or None,
                                temperature=0.0,  # ADR-001: agents run at temp=0
                            )
                        except Exception as _log_exc:
                            logger.debug("llm_logger.failed: %s", _log_exc)
                        return result
                    except anthropic.APIStatusError as e:
                        # APIStatusError covers 4xx and 5xx — split into
                        # transient (429, 5xx) and permanent (other 4xx).
                        if _is_transient_error(e) and attempt_in_model < _RETRY_MAX_ATTEMPTS_PER_MODEL:
                            backoff_s = _RETRY_BACKOFF_BASE_S * (
                                _RETRY_BACKOFF_FACTOR ** (attempt_in_model - 1)
                            )
                            logger.warning(
                                "llm.transient_error phase=%s model=%s attempt=%d/%d — "
                                "sleeping %.1fs and retrying same model. err=%s",
                                self.phase_number, fallback_model,
                                attempt_in_model, _RETRY_MAX_ATTEMPTS_PER_MODEL,
                                backoff_s, str(e)[:200],
                                extra={"phase": self.phase_number, "model": fallback_model},
                            )
                            last_error = e
                            await asyncio.sleep(backoff_s)
                            continue  # retry SAME model
                        # Either non-transient OR exhausted retries — give up
                        # on this model and try the next.
                        if _is_transient_error(e):
                            logger.warning(
                                "llm.transient_error_exhausted phase=%s model=%s - "
                                "exhausted %d retries, sleeping %.1fs before next provider",
                                self.phase_number, fallback_model,
                                _RETRY_MAX_ATTEMPTS_PER_MODEL,
                                _INTER_PROVIDER_BACKOFF_S,
                                extra={"phase": self.phase_number},
                            )
                            await asyncio.sleep(_INTER_PROVIDER_BACKOFF_S)
                        else:
                            logger.warning(
                                "llm.permanent_error phase=%s model=%s — "
                                "non-retryable, falling through to next model. err=%s",
                                self.phase_number, fallback_model, str(e)[:200],
                                extra={"phase": self.phase_number},
                            )
                        last_error = e
                        break  # next model in chain
                    except Exception as e:
                        # Non-Anthropic exceptions (httpx errors, OpenAI SDK
                        # rate limits via DeepSeek path, RuntimeError etc.).
                        if _is_transient_error(e) and attempt_in_model < _RETRY_MAX_ATTEMPTS_PER_MODEL:
                            backoff_s = _RETRY_BACKOFF_BASE_S * (
                                _RETRY_BACKOFF_FACTOR ** (attempt_in_model - 1)
                            )
                            logger.warning(
                                "llm.transient_error phase=%s model=%s attempt=%d/%d — "
                                "sleeping %.1fs and retrying same model. err=%s",
                                self.phase_number, fallback_model,
                                attempt_in_model, _RETRY_MAX_ATTEMPTS_PER_MODEL,
                                backoff_s, str(e)[:200],
                                extra={"phase": self.phase_number, "model": fallback_model},
                            )
                            last_error = e
                            await asyncio.sleep(backoff_s)
                            continue
                        logger.warning(
                            "llm.error phase=%s model=%s: %s — trying next",
                            self.phase_number, fallback_model, e,
                            extra={"phase": self.phase_number},
                        )
                        last_error = e
                        break  # next model in chain

            err = RuntimeError(
                f"All models in fallback chain failed. Last error: {last_error}"
            )
            span.record_exception(err)
            try:
                from opentelemetry.trace import Status, StatusCode
                span.set_status(Status(StatusCode.ERROR))
            except Exception:
                pass
            raise err

    async def _call_model(
        self,
        model: str,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
        tool_choice: Optional[dict] = None,
    ) -> Optional[dict]:
        """Route to the correct API based on model name."""

        if model.startswith("claude"):
            return await self._call_anthropic(model, messages, system, tools, max_tokens, tool_choice)
        elif model.startswith("deepseek"):
            return await self._call_deepseek(model, messages, system, tools, max_tokens, tool_choice)
        elif model.startswith("ollama"):
            return await self._call_ollama(model, messages, system, max_tokens)
        elif model.startswith("glm"):
            # GLM via Z.AI uses Anthropic-compatible API — full tool_use support
            return await self._call_glm_anthropic(model, messages, system, tools, max_tokens, tool_choice)
        else:
            logger.warning(f"Unknown model type: {model}")
            return None

    async def _call_anthropic(
        self,
        model: str,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
        tool_choice: Optional[dict] = None,
    ) -> dict:
        """Call Claude API with native tool_use."""
        if not self._anthropic_client:
            raise RuntimeError("Anthropic client not initialized (missing API key)")

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

        # Run the synchronous Anthropic client in a thread pool executor so it
        # does NOT block the FastAPI event loop during long LLM calls.
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: self._anthropic_client.messages.create(**kwargs)
        )

        # Parse response
        content_text = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }

    async def _call_deepseek(
        self,
        model: str,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
        tool_choice: Optional[dict] = None,
    ) -> dict:
        """Call DeepSeek API (OpenAI-compatible).

        DeepSeek-V3 ('deepseek-chat') supports function/tool calling via the
        OpenAI tools schema.  Tool definitions are converted from Anthropic
        format → OpenAI format on the fly.
        """
        if not self._deepseek_client:
            raise RuntimeError("DeepSeek client not initialized (missing DEEPSEEK_API_KEY)")

        # Build message list with optional system prompt
        oai_messages: list[dict] = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)

        # Convert Anthropic tool schema → OpenAI tool schema
        oai_tools = []
        for t in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            })

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools
            # Convert Anthropic tool_choice → OpenAI tool_choice format
            if tool_choice and tool_choice.get("type") == "tool":
                kwargs["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice["name"]},
                }
            elif tool_choice and tool_choice.get("type") == "any":
                kwargs["tool_choice"] = "required"  # OpenAI equivalent of Anthropic "any"
            else:
                kwargs["tool_choice"] = "auto"

        response = await self._deepseek_client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        msg = choice.message

        content_text = msg.content or ""
        tool_calls = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    input_data = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    input_data = {"raw": tc.function.arguments}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": input_data,
                })

        # Map OpenAI finish_reason → Anthropic stop_reason for compatibility
        finish_map = {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
            "content_filter": "end_turn",
        }
        stop_reason = finish_map.get(choice.finish_reason or "stop", "end_turn")

        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
            },
            "model_used": model,
        }

    async def _call_ollama(
        self,
        model: str,
        messages: list[dict],
        system: str,
        max_tokens: int,
    ) -> dict:
        """Call Ollama local API for air-gapped mode."""
        ollama_model = model.replace("ollama/", "")

        ollama_messages = []
        if system:
            ollama_messages.append({"role": "system", "content": system})
        ollama_messages.extend(messages)

        _proxy = _get_proxy()
        _client_kwargs: dict = {"timeout": 120.0}
        # Ollama is local — never route through proxy
        if _proxy and not _is_no_proxy("localhost"):
            _client_kwargs["proxy"] = _proxy
            _client_kwargs["verify"] = _get_ssl_verify()
        async with httpx.AsyncClient(**_client_kwargs) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": ollama_messages,
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
            )
            response.raise_for_status()
            data = await response.json()

        return {
            "content": data.get("message", {}).get("content", ""),
            "tool_calls": [],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": data.get("prompt_eval_count", 0),
                "output_tokens": data.get("eval_count", 0),
            },
        }

    async def _call_glm_anthropic(
        self,
        model: str,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
        tool_choice: Optional[dict] = None,
    ) -> dict:
        """
        Call GLM via Z.AI using the Anthropic-compatible endpoint.
        Z.AI exposes https://api.z.ai/api/anthropic which speaks the Anthropic SDK
        protocol — so we get native tool_use, streaming, and the same response format.
        """
        if not settings.glm_api_key:
            raise RuntimeError("GLM API key not configured")

        # Create a one-off Anthropic client pointed at Z.AI (bypass proxy via NO_PROXY)
        _hc = _make_sync_httpx_client("api.z.ai")
        glm_client = anthropic.Anthropic(
            api_key=settings.glm_api_key,
            base_url=settings.glm_base_url,
            **( {"http_client": _hc} if _hc else {} ),
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

        # Run the synchronous GLM client in a thread pool executor so it
        # does NOT block the FastAPI event loop during long LLM calls.
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: glm_client.messages.create(**kwargs)
        )

        content_text = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }

    async def _call_glm(
        self,
        model: str,
        messages: list[dict],
        system: str,
        max_tokens: int,
    ) -> dict:
        """
        Call GLM via OpenAI-compatible API (legacy fallback, no tool_use).
        Used only if Z.AI endpoint is unavailable.
        """
        if not settings.glm_api_key:
            raise RuntimeError("GLM API key not configured")

        glm_messages = []
        if system:
            glm_messages.append({"role": "system", "content": system})
        glm_messages.extend(messages)

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{settings.glm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.glm_api_key}"},
                json={
                    "model": model,
                    "messages": glm_messages,
                    "max_tokens": max_tokens,
                },
            )
            response.raise_for_status()
            data = response.json() if not hasattr(response.json, "__await__") else await response.json()

        choice = data.get("choices", [{}])[0]
        return {
            "content": choice.get("message", {}).get("content", ""),
            "tool_calls": [],
            "stop_reason": choice.get("finish_reason", "stop"),
            "usage": data.get("usage", {}),
        }

    async def call_llm_with_tools(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tool_handlers: Optional[dict] = None,
        max_iterations: int = 10,
        terminal_tools: Optional[set] = None,
        tool_choice: Optional[dict] = None,
    ) -> dict:
        """
        Call Claude with tool_use and automatically handle tool calls in a loop.

        Args:
            messages: Conversation messages.
            system: System prompt.
            tool_handlers: Dict mapping tool names to async handler functions.
            max_iterations: Max tool call iterations to prevent infinite loops.
            terminal_tools: Set of tool names that stop the loop immediately when called.
            tool_choice: Optional tool_choice dict to force a specific tool on the FIRST iteration only.

        Returns:
            Final response dict with accumulated content.
        """
        system = system or self.system_prompt
        tool_handlers = tool_handlers or {}
        terminal_tools = terminal_tools or set()
        accumulated_content = ""
        current_messages = list(messages)

        for iteration in range(max_iterations):
            # tool_choice is only applied on the first iteration; subsequent iterations
            # allow the model to respond freely (otherwise it loops on the same tool).
            _tc = tool_choice if iteration == 0 else None
            response = await self.call_llm(
                messages=current_messages,
                system=system,
                tool_choice=_tc,
            )

            accumulated_content += response.get("content", "")

            # If no tool calls, we're done
            if not response.get("tool_calls"):
                response["content"] = accumulated_content
                return response

            # Process each tool call
            # Add assistant message with tool use
            assistant_content = []
            if response.get("content"):
                assistant_content.append({"type": "text", "text": response["content"]})
            for tc in response["tool_calls"]:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            current_messages.append({"role": "assistant", "content": assistant_content})

            # Execute tool handlers and collect results
            tool_results = []
            for tc in response["tool_calls"]:
                handler = tool_handlers.get(tc["name"])
                if handler:
                    try:
                        result = await handler(tc["input"])
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": json.dumps(result) if isinstance(result, (dict, list)) else str(result),
                        })
                    except Exception as e:
                        logger.error(f"Tool {tc['name']} failed: {e}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": f"Error: {str(e)}",
                            "is_error": True,
                        })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": f"Tool '{tc['name']}' not found",
                        "is_error": True,
                    })

            current_messages.append({"role": "user", "content": tool_results})

            # If any terminal tool was called, stop the loop immediately —
            # no need for the model to write a follow-up summary.
            # IMPORTANT: Discard accumulated preamble text (e.g. "I have all the
            # information...") so it doesn't leak into the caller's response and
            # appear as an unwanted chat bubble. The caller (requirements_agent)
            # builds its own rich summary from the tool input data.
            called_names = {tc["name"] for tc in response["tool_calls"]}
            if terminal_tools and called_names & terminal_tools:
                response["content"] = ""  # drop LLM preamble — caller builds its own response
                return response

        # Max iterations reached
        response["content"] = accumulated_content
        return response

    def log(self, message: str, level: str = "info", **extra):
        """
        Structured log with phase context.
        Extra kwargs are included as structured fields (project_id, model, etc.).
        """
        extra["phase"] = self.phase_number
        # Use the logging extra dict so formatters can pick up structured fields
        getattr(logger, level)(
            "[%s:%s] %s", self.phase_number, self.phase_name, message,
            extra=extra,
            stacklevel=2,
        )
