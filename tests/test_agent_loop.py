"""End-to-end agent loop, offline (mock model + synthetic backend, direct tools)."""

import json
from pathlib import Path

import pytest

from echo_sre.agent.loop import AgentRunner
from echo_sre.inference.gateway import InferenceGateway
from echo_sre.inference.mock import MockProvider

_SCENARIO = json.loads(
    (Path(__file__).resolve().parents[1] / "scenarios" / "checkout_latency.json").read_text()
)


@pytest.mark.asyncio
async def test_investigation_reaches_root_cause():
    gw = InferenceGateway([MockProvider()])
    runner = AgentRunner(gw, use_mcp=False, scenario=_SCENARIO, max_steps=8)

    result = await runner.investigate(_SCENARIO["alert"])

    called = {tc.name for tc in result.tool_calls}
    # The agent gathered metrics, walked topology, read logs, and consulted a runbook.
    assert {"query_metrics", "search_logs", "get_runbook"}.issubset(called)
    # The verdict names the actual root cause area.
    text = result.summary.lower()
    assert "postgres" in text or "connection" in text
    assert result.steps <= 8
    # Runbook citation surfaced for the UI's source badges.
    assert any(s.startswith("runbook:") for s in result.sources)


@pytest.mark.asyncio
async def test_stream_emits_tool_and_final_events():
    gw = InferenceGateway([MockProvider()])
    runner = AgentRunner(gw, use_mcp=False, scenario=_SCENARIO, max_steps=8)

    types = []
    async for ev in runner.stream(_SCENARIO["alert"]):
        types.append(ev.type)

    assert "tool_call" in types
    assert "tool_result" in types
    assert "final" in types
    assert types[-1] == "done"
