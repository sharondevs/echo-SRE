"""ECHO-SRE MCP server.

Exposes the observability toolset over the Model Context Protocol so ANY MCP client —
Claude Desktop, Cursor, or the ECHO-SRE agent itself — can investigate incidents. The
tools delegate to the shared registry in :mod:`echo_sre.tools`, so the agent and external
clients call exactly the same implementations.

Run standalone:  echo-sre serve-mcp        (stdio; default)
                 echo-sre serve-mcp --http  (streamable-http)
Backend is chosen by ECHO_SRE_BACKEND (synthetic | prometheus).
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from ..observability.factory import load_scenario
from ..tools import ToolContext, call_tool

mcp = FastMCP("echo-sre")

# A single context is fine: tools are stateless reads against the backend/runbooks.
# ECHO_SRE_SCENARIO lets a parent (e.g. the agent) pin which synthetic incident to serve.
_ctx = ToolContext.default(scenario=load_scenario(os.getenv("ECHO_SRE_SCENARIO")))


@mcp.tool()
async def list_alerts(active_only: bool = True) -> list[dict]:
    """List currently firing alerts with severity, owning service, and a summary."""
    return await call_tool(_ctx, "list_alerts", {"active_only": active_only})


@mcp.tool()
async def query_metrics(query: str, minutes: int = 30) -> list[dict]:
    """Query time-series metrics (PromQL-style). Returns per-series summary stats."""
    return await call_tool(_ctx, "query_metrics", {"query": query, "minutes": minutes})


@mcp.tool()
async def get_service_topology() -> list[dict]:
    """Return the service dependency graph (blast radius / upstream-cause reasoning)."""
    return await call_tool(_ctx, "get_service_topology", {})


@mcp.tool()
async def search_logs(
    service: str | None = None, pattern: str | None = None, minutes: int = 30, limit: int = 100
) -> list[dict]:
    """Search recent service logs by service name and/or substring pattern."""
    return await call_tool(
        _ctx, "search_logs", {"service": service, "pattern": pattern, "minutes": minutes, "limit": limit}
    )


@mcp.tool()
async def get_runbook(query: str, k: int = 3) -> list[dict]:
    """Retrieve the most relevant runbook sections for a symptom or remediation step."""
    return await call_tool(_ctx, "get_runbook", {"query": query, "k": k})


def main() -> None:
    transport = os.getenv("ECHO_SRE_MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
