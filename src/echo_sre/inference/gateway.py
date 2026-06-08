"""The inference gateway — the scalable spine.

Routes every chat request through an ordered list of providers, AUTOMATICALLY FALLING
BACK to the next provider on transient failure, with per-provider retries + exponential
backoff, token-budget trimming per provider window, and Prometheus metrics throughout.

This generalizes the "LLM routing with automatic fallback" pattern into a reusable,
provider-agnostic component: a local vLLM and any hosted OpenAI-compatible API
(Gemini/OpenAI/Groq) are interchangeable links in the same chain.
"""

from __future__ import annotations

import asyncio
import random
from typing import AsyncIterator

from ..config import GatewayDefaults, load_providers
from . import metrics as M
from .budget import trim_to_budget
from .mock import MockProvider
from .provider import Provider, ProviderError
from .types import ChatResponse, Message, ToolSpec


class AllProvidersFailed(Exception):
    """Raised when every provider in the chain has been exhausted."""


class InferenceGateway:
    def __init__(self, providers: list, defaults: GatewayDefaults | None = None):
        # An empty chain means "no usable provider configured" -> offline mock model.
        self.providers = providers or [MockProvider()]
        self.defaults = defaults or GatewayDefaults()

    @classmethod
    def from_config(cls, path: str | None = None) -> "InferenceGateway":
        cfgs, defaults = load_providers(path)
        usable = [Provider(c) for c in cfgs if c.is_usable]
        return cls(usable, defaults)

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.providers]

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """Try providers in priority order; fail over automatically on transient errors."""
        temp = self.defaults.temperature if temperature is None else temperature
        out_tokens = self.defaults.max_tokens if max_tokens is None else max_tokens
        last_err: Exception | None = None

        for i, provider in enumerate(self.providers):
            max_ctx = getattr(provider.cfg, "max_context_tokens", 8192)
            trimmed = trim_to_budget(messages, max_ctx, reserve_output=out_tokens)

            for attempt in range(self.defaults.retries_per_provider + 1):
                try:
                    resp = await provider.chat(trimmed, tools, temp, out_tokens)
                    self._record_success(provider, resp)
                    return resp
                except ProviderError as err:
                    last_err = err
                    outcome = "retryable_error" if err.retryable else "fatal_error"
                    M.LLM_REQUESTS.labels(provider.name, _model(provider), outcome).inc()
                    if not err.retryable:
                        break  # don't retry the same provider; move to the next one
                    if attempt < self.defaults.retries_per_provider:
                        await asyncio.sleep(self._backoff(attempt))

            # Exhausted this provider; record the failover to the next link (if any).
            if i + 1 < len(self.providers):
                M.LLM_FALLBACKS.labels(provider.name, self.providers[i + 1].name).inc()

        raise AllProvidersFailed(str(last_err) if last_err else "no providers available")

    async def stream_final(
        self,
        messages: list[Message],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream the final answer as text deltas.

        Fails over to the next provider only *before* the first token is emitted (once
        bytes are on the wire we cannot transparently switch providers mid-stream).
        """
        temp = self.defaults.temperature if temperature is None else temperature
        out_tokens = self.defaults.max_tokens if max_tokens is None else max_tokens

        for i, provider in enumerate(self.providers):
            max_ctx = getattr(provider.cfg, "max_context_tokens", 8192)
            trimmed = trim_to_budget(messages, max_ctx, reserve_output=out_tokens)
            started = False
            try:
                async for chunk in provider.stream(trimmed, None, temp, out_tokens):
                    if chunk.delta_text:
                        started = True
                        yield chunk.delta_text
                M.LLM_REQUESTS.labels(provider.name, _model(provider), "success").inc()
                return
            except ProviderError as err:
                M.LLM_REQUESTS.labels(provider.name, _model(provider), "retryable_error").inc()
                if started:
                    raise  # already streaming to the client; cannot fail over now
                if i + 1 < len(self.providers):
                    M.LLM_FALLBACKS.labels(provider.name, self.providers[i + 1].name).inc()
                continue
        raise AllProvidersFailed("no provider could stream the final answer")

    def _record_success(self, provider, resp: ChatResponse) -> None:
        M.LLM_REQUESTS.labels(provider.name, _model(provider), "success").inc()
        M.LLM_LATENCY.labels(provider.name).observe(resp.latency_s)
        M.LLM_TOKENS.labels(provider.name, "prompt").inc(resp.usage.prompt_tokens)
        M.LLM_TOKENS.labels(provider.name, "completion").inc(resp.usage.completion_tokens)

    def _backoff(self, attempt: int) -> float:
        return self.defaults.backoff_base_s * (2**attempt) + random.uniform(0, 0.25)


def _model(provider) -> str:
    return getattr(provider.cfg, "model", "unknown")
