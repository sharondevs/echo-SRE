"""The SRE investigation agent — a ReAct loop driven entirely over MCP.

The agent ALWAYS talks to its tools through a real Model Context Protocol
``ClientSession``: for every investigation it spawns the ECHO-SRE MCP server as a stdio
subprocess and calls tools through it. There is no in-process shortcut — MCP is the only
path, which is what makes this an authentic MCP system.

Because the server is launched per investigation, each run can be pointed at a different
backend by passing environment to the subprocess: a custom synthetic ``scenario`` (written
to a temp file) or a live Prometheus/Loki/Alertmanager stack (``backend_env``). That is how
the portfolio's "SRE mode" supports Demo / Custom-scenario / Live without any shared state.

The loop drives the inference gateway (with automatic provider fallback) and exposes both
a one-shot :meth:`AgentRunner.investigate` and a streaming :meth:`AgentRunner.stream` that
yields :class:`AgentEvent`s for the SSE endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from ..inference import InferenceGateway, Message, ToolCall, ToolSpec, Usage
from ..inference.metrics import AGENT_STEPS, AGENT_TOOL_CALLS
from .prompts import SRE_SYSTEM_PROMPT

EmitFn = Callable[["AgentEvent"], Awaitable[None]]


class AgentEvent(BaseModel):
    """A streamed step of the investigation (consumed by the SSE endpoint / CLI)."""

    type: Literal["status", "tool_call", "tool_result", "token", "final", "done", "error"]
    text: str = ""
    tool: str | None = None
    args: dict | None = None
    sources: list[str] | None = None


class IncidentResult(BaseModel):
    summary: str
    transcript: list[Message] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    steps: int = 0
    usage_total: Usage = Field(default_factory=Usage)
    providers_used: list[str] = Field(default_factory=list)


class _MCPTools:
    """Tools served by the ECHO-SRE MCP server over a stdio ClientSession."""

    def __init__(self, session, specs: list[ToolSpec]):
        self._session = session
        self.specs = specs

    async def invoke(self, name: str, args: dict) -> str:
        result = await self._session.call_tool(name, args or {})
        # Prefer MCP structured output: FastMCP returns list/dict tools as
        # structuredContent (lists are wrapped as {"result": [...]}). This gives a clean
        # JSON payload for both the LLM and our own parsing, instead of N text blocks.
        sc = getattr(result, "structuredContent", None)
        if isinstance(sc, dict):
            data = sc["result"] if set(sc.keys()) == {"result"} else sc
            return json.dumps(data, default=str)
        parts = [getattr(b, "text", None) for b in result.content]
        parts = [p for p in parts if p is not None]
        return "\n".join(parts) if parts else "[]"


@asynccontextmanager
async def _open_tools(scenario: dict | None, backend_env: dict[str, str] | None):
    """Spawn the ECHO-SRE MCP server and yield a tool transport bound to it.

    ``scenario`` (a synthetic incident) is written to a temp file and pointed at via
    ``ECHO_SRE_SCENARIO``. ``backend_env`` overrides backend selection for this run
    (e.g. ``ECHO_SRE_BACKEND=prometheus`` + ``ECHO_SRE_PROM_URL``/``ECHO_SRE_LOKI_URL``).
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    if backend_env:
        env.update(backend_env)

    tmp_path: str | None = None
    if scenario is not None:
        fd, tmp_path = tempfile.mkstemp(prefix="echo_sre_scn_", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(scenario, fh)
        env["ECHO_SRE_SCENARIO"] = tmp_path
        env.setdefault("ECHO_SRE_BACKEND", "synthetic")

    try:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "echo_sre.mcp_server.server"], env=env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                specs = [
                    ToolSpec(
                        name=t.name,
                        description=t.description or "",
                        parameters=t.inputSchema or {"type": "object", "properties": {}},
                    )
                    for t in listed.tools
                ]
                yield _MCPTools(session, specs)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


class AgentRunner:
    def __init__(
        self,
        gateway: InferenceGateway,
        *,
        max_steps: int = 8,
        scenario: dict | None = None,
        backend_env: dict[str, str] | None = None,
    ):
        self.gateway = gateway
        self.max_steps = max_steps
        self.scenario = scenario
        self.backend_env = backend_env

    async def investigate(self, alert: str) -> IncidentResult:
        """Run the investigation to completion (no streaming)."""
        return await self._run(alert, emit=None)

    async def stream(self, alert: str) -> AsyncIterator[AgentEvent]:
        """Run the investigation, yielding events as they happen via a queue."""
        q: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        async def emit(ev: AgentEvent) -> None:
            await q.put(ev)

        async def runner() -> None:
            try:
                await self._run(alert, emit=emit)
            except Exception as exc:  # noqa: BLE001 - surface as a stream error event
                await q.put(AgentEvent(type="error", text=str(exc)))
            finally:
                await q.put(None)

        task = asyncio.create_task(runner())
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield ev
        finally:
            await task

    async def _run(self, alert: str, emit: EmitFn | None) -> IncidentResult:
        async def _emit(ev: AgentEvent) -> None:
            if emit is not None:
                await emit(ev)

        messages: list[Message] = [
            Message(role="system", content=SRE_SYSTEM_PROMPT),
            Message(role="user", content=f"Investigate this alert:\n{alert}"),
        ]
        all_tool_calls: list[ToolCall] = []
        sources: list[str] = []
        usage_total = Usage()
        providers_used: list[str] = []
        summary = ""

        async with _open_tools(self.scenario, self.backend_env) as tools:
            await _emit(AgentEvent(type="status", text="Investigation started"))
            for _step in range(self.max_steps):
                resp = await self.gateway.chat(messages, tools=tools.specs)
                usage_total = usage_total + resp.usage
                if resp.provider:
                    providers_used.append(resp.provider)
                messages.append(resp.message)

                if resp.message.tool_calls:
                    for tc in resp.message.tool_calls:
                        AGENT_TOOL_CALLS.labels(tc.name).inc()
                        AGENT_STEPS.labels("tool_call").inc()
                        all_tool_calls.append(tc)
                        if tc.name not in sources:
                            sources.append(tc.name)
                        await _emit(
                            AgentEvent(
                                type="tool_call", tool=tc.name, args=tc.arguments,
                                text=f"{tc.name}({_fmt_args(tc.arguments)})",
                            )
                        )
                        out = await tools.invoke(tc.name, tc.arguments)
                        messages.append(
                            Message(role="tool", tool_call_id=tc.id, name=tc.name, content=out)
                        )
                        await _emit(AgentEvent(type="tool_result", tool=tc.name, text=_preview(out)))
                    continue

                # No tool calls -> final answer.
                AGENT_STEPS.labels("final").inc()
                summary = resp.message.content or ""
                for token in _chunk(summary):
                    await _emit(AgentEvent(type="token", text=token))
                break
            else:
                AGENT_STEPS.labels("max_steps").inc()
                summary = "Investigation hit the step limit before reaching a conclusion."
                await _emit(AgentEvent(type="token", text=summary))

            sources = _dedupe(sources + _runbook_sources(messages))
            await _emit(AgentEvent(type="final", text=summary, sources=sources))
            await _emit(AgentEvent(type="done", sources=sources))

        return IncidentResult(
            summary=summary,
            transcript=messages,
            tool_calls=all_tool_calls,
            sources=sources,
            steps=len(all_tool_calls),
            usage_total=usage_total,
            providers_used=providers_used,
        )


def _fmt_args(args: dict | None) -> str:
    if not args:
        return ""
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def _preview(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " …"


def _chunk(text: str):
    """Split the final answer into small pieces for a typewriter SSE effect."""
    buf = ""
    for word in text.split(" "):
        buf += word + " "
        if len(buf) >= 24 or "\n" in word:
            yield buf
            buf = ""
    if buf:
        yield buf


def _runbook_sources(messages: list[Message]) -> list[str]:
    """Pull runbook titles out of get_runbook tool results so the UI can cite them."""
    out: list[str] = []
    for m in messages:
        if m.role == "tool" and m.name == "get_runbook" and m.content:
            try:
                for hit in json.loads(m.content):
                    title = hit.get("title")
                    if title:
                        out.append(f"runbook: {title}")
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
    return out


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out
