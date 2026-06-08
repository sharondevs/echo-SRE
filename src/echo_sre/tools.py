"""Canonical observability tool registry.

Defines each tool ONCE — its JSON-Schema spec (for the LLM) and its execution against an
:class:`ObservabilityBackend` + runbook index. Both consumers reuse these:
  * the MCP server (:mod:`echo_sre.mcp_server.server`) wraps them as ``@mcp.tool``s, and
  * the agent's direct (no-MCP) fast path calls :func:`call_tool` in-process.
This guarantees the tools the agent reasons over are identical to the ones exposed via MCP.
"""

from __future__ import annotations

from dataclasses import dataclass

from .inference.types import ToolSpec
from .observability.base import ObservabilityBackend
from .observability.factory import build_backend
from .rag.runbooks import RunbookIndex


@dataclass
class ToolContext:
    backend: ObservabilityBackend
    runbooks: RunbookIndex

    @classmethod
    def default(cls, scenario: dict | None = None) -> "ToolContext":
        return cls(backend=build_backend(scenario=scenario), runbooks=RunbookIndex.load())


# -- JSON-Schema specs advertised to the model ---------------------------------
TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="list_alerts",
        description="List currently firing alerts with severity, owning service, and a summary.",
        parameters={
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "description": "Only firing alerts.", "default": True}
            },
        },
    ),
    ToolSpec(
        name="query_metrics",
        description=(
            "Query time-series metrics (PromQL-style, e.g. "
            'http_request_duration_p95{service="checkout"}). Returns per-series summary '
            "stats (latest/min/max/avg) over the last N minutes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL-style metric query."},
                "minutes": {"type": "integer", "description": "Look-back window.", "default": 30},
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="get_service_topology",
        description="Return the service dependency graph to reason about blast radius and upstream causes.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="search_logs",
        description="Search recent service logs by service name and/or a substring pattern.",
        parameters={
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service to filter by."},
                "pattern": {"type": "string", "description": "Case-insensitive substring to match."},
                "minutes": {"type": "integer", "default": 30},
                "limit": {"type": "integer", "default": 100},
            },
        },
    ),
    ToolSpec(
        name="get_runbook",
        description="Retrieve the most relevant runbook sections for a symptom or remediation step.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Symptom / remediation query."},
                "k": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    ),
]

TOOL_NAMES = [t.name for t in TOOL_SPECS]


# -- Execution -----------------------------------------------------------------
async def call_tool(ctx: ToolContext, name: str, args: dict) -> object:
    """Execute a tool by name and return a JSON-serializable result."""
    args = args or {}
    if name == "list_alerts":
        alerts = await ctx.backend.list_alerts(bool(args.get("active_only", True)))
        return [a.model_dump() for a in alerts]
    if name == "query_metrics":
        series = await ctx.backend.query_metrics(args["query"], int(args.get("minutes", 30)))
        return [s.summary() for s in series]
    if name == "get_service_topology":
        nodes = await ctx.backend.service_topology()
        return [n.model_dump() for n in nodes]
    if name == "search_logs":
        logs = await ctx.backend.search_logs(
            service=args.get("service"),
            pattern=args.get("pattern"),
            minutes=int(args.get("minutes", 30)),
            limit=int(args.get("limit", 100)),
        )
        return [log.model_dump() for log in logs]
    if name == "get_runbook":
        hits = ctx.runbooks.search(args["query"], int(args.get("k", 3)))
        return [h.model_dump() for h in hits]
    raise ValueError(f"unknown tool: {name}")
