"""A deterministic, offline mock model.

It implements the same ``chat``/``stream`` surface as :class:`Provider` but never hits
the network, so ``echo-sre demo`` and the test-suite exercise the *entire* agent loop —
tool calling over MCP, budgeting, metrics — with no API keys. It scripts a realistic SRE
investigation: pull metrics -> inspect topology -> search logs -> consult a runbook ->
deliver a structured root-cause verdict.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from .types import ChatResponse, Message, StreamChunk, ToolCall, ToolSpec, Usage

# Order in which the mock "investigates", filtered by the tools actually offered.
_PLAYBOOK: list[tuple[str, dict]] = [
    ("list_alerts", {"active_only": True}),
    ("query_metrics", {"query": 'http_request_duration_p95{service="checkout"}', "minutes": 30}),
    ("get_service_topology", {}),
    ("search_logs", {"service": "payments", "pattern": "", "minutes": 30}),
    ("get_runbook", {"query": "database connection pool exhaustion latency"}),
]

_FINAL_VERDICT = """\
## Root Cause
The checkout p95 latency spike is caused by **PostgreSQL connection-pool exhaustion**.
`payments` depends on `postgres`, and its connection pool saturated, so requests block
waiting for a free connection. The latency surfaces upstream at `checkout` -> `gateway`.

## Evidence
- `query_metrics`: `checkout` p95 latency is ~5x baseline, starting ~12 min ago.
- `get_service_topology`: gateway -> checkout -> payments -> postgres (blast radius is the
  checkout path; the deepest unhealthy dependency is postgres).
- `search_logs`: `payments` logs show "db timeout acquiring connection"; `postgres` logs
  show "FATAL: too many connections".
- `get_runbook`: matches the *Database connection-pool exhaustion* runbook.

## Remediation
1. Immediately raise the `payments` pool ceiling / scale `payments` replicas to shed the
   queue, or bump postgres `max_connections` if headroom exists.
2. Add a PgBouncer connection pooler in front of postgres to bound total connections.
3. Investigate the connection leak / slow query that triggered saturation (check long
   transactions and recent `payments` deploys).

## Confidence
High — the metric anomaly, dependency path, and correlated logs agree, and the symptom
matches a known runbook.
"""


class MockProvider:
    """Scripted, network-free stand-in for :class:`Provider`."""

    name = "mock"

    class _Cfg:
        name = "mock"
        model = "mock-sre-1"
        max_context_tokens = 8192

    def __init__(self):
        self.cfg = MockProvider._Cfg()

    @staticmethod
    def _already_called(messages: list[Message]) -> set[str]:
        called: set[str] = set()
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                called.update(tc.name for tc in m.tool_calls)
        return called

    def _next_action(self, messages: list[Message], tools: list[ToolSpec] | None):
        available = {t.name for t in tools} if tools else set()
        called = self._already_called(messages)
        for name, args in _PLAYBOOK:
            if name in available and name not in called:
                return name, args
        return None

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> ChatResponse:
        action = self._next_action(messages, tools)
        if action is not None:
            name, args = action
            tc = ToolCall(id=f"call_{name}", name=name, arguments=args)
            msg = Message(role="assistant", content=None, tool_calls=[tc])
            usage = Usage(prompt_tokens=200, completion_tokens=20, total_tokens=220)
            return ChatResponse(
                message=msg, usage=usage, provider="mock", model="mock-sre-1",
                latency_s=0.01, finish_reason="tool_calls",
            )
        msg = Message(role="assistant", content=_FINAL_VERDICT)
        usage = Usage(prompt_tokens=400, completion_tokens=160, total_tokens=560)
        return ChatResponse(
            message=msg, usage=usage, provider="mock", model="mock-sre-1",
            latency_s=0.01, finish_reason="stop",
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        # Streaming is only used for the final answer; emit the verdict word by word.
        for word in _FINAL_VERDICT.split(" "):
            yield StreamChunk(delta_text=word + " ")
        yield StreamChunk(done=True, usage=Usage(prompt_tokens=400, completion_tokens=160, total_tokens=560))
