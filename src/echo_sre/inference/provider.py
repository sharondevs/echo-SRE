"""A single inference backend behind the OpenAI Chat Completions API.

Because vLLM and every hosted OpenAI-compatible API (Gemini, OpenAI, Groq, Together)
speak the same protocol, one async client serves them all — only base_url/model/key
differ. Errors are classified as retryable (transient) or fatal so the gateway knows
whether to back off and retry, or fail over to the next provider immediately.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from ..config import ProviderConfig
from .types import ChatResponse, Message, StreamChunk, ToolCall, ToolSpec, Usage


class ProviderError(Exception):
    """Wraps a provider failure with a retryable/fatal classification."""

    def __init__(self, provider: str, message: str, *, retryable: bool):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retryable = retryable


def _to_openai_messages(messages: list[Message]) -> list[dict]:
    """Serialize provider-neutral messages into OpenAI wire format."""
    out: list[dict] = []
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": _dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        elif m.role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id or "",
                    "content": m.content or "",
                }
            )
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


def _dumps(obj) -> str:
    import json

    return obj if isinstance(obj, str) else json.dumps(obj)


def _parse_tool_calls(raw_tool_calls) -> list[ToolCall] | None:
    import json

    if not raw_tool_calls:
        return None
    parsed: list[ToolCall] = []
    for tc in raw_tool_calls:
        fn = tc.function
        try:
            args = json.loads(fn.arguments) if fn.arguments else {}
        except (json.JSONDecodeError, TypeError):
            args = {"_raw": fn.arguments}
        parsed.append(ToolCall(id=tc.id or f"call_{len(parsed)}", name=fn.name, arguments=args))
    return parsed or None


class Provider:
    """OpenAI-compatible chat provider (works for vLLM and any hosted API)."""

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self._client = AsyncOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.resolve_api_key(),
            timeout=cfg.timeout_s,
            max_retries=0,  # the gateway owns retry/backoff & fallback
        )

    @property
    def name(self) -> str:
        return self.cfg.name

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> ChatResponse:
        start = time.perf_counter()
        kwargs: dict = {
            "model": self.cfg.model,
            "messages": _to_openai_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = [t.to_openai() for t in tools]
            kwargs["tool_choice"] = "auto"

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - classify then re-raise as ProviderError
            raise self._classify(exc) from exc

        choice = resp.choices[0]
        usage = Usage(
            prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(resp.usage, "total_tokens", 0) or 0,
        )
        message = Message(
            role="assistant",
            content=choice.message.content,
            tool_calls=_parse_tool_calls(getattr(choice.message, "tool_calls", None)),
        )
        return ChatResponse(
            message=message,
            usage=usage,
            provider=self.cfg.name,
            model=self.cfg.model,
            latency_s=time.perf_counter() - start,
            finish_reason=choice.finish_reason or "stop",
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        """Stream text deltas. Used for the final answer (tool steps use ``chat``)."""
        kwargs: dict = {
            "model": self.cfg.model,
            "messages": _to_openai_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [t.to_openai() for t in tools]
            kwargs["tool_choice"] = "auto"
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for event in stream:
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                text = getattr(delta, "content", None) or ""
                if text:
                    yield StreamChunk(delta_text=text)
            yield StreamChunk(done=True)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc

    def _classify(self, exc: Exception) -> ProviderError:
        retryable = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)
        fatal = (AuthenticationError, PermissionDeniedError, BadRequestError, NotFoundError)
        if isinstance(exc, retryable):
            return ProviderError(self.cfg.name, repr(exc), retryable=True)
        if isinstance(exc, fatal):
            return ProviderError(self.cfg.name, repr(exc), retryable=False)
        # Unknown errors: treat as retryable so the gateway can still fail over.
        return ProviderError(self.cfg.name, repr(exc), retryable=True)
