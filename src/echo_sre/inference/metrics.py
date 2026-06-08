"""Prometheus metrics for the inference gateway and agent loop.

These are scrape-ready for the second-pass Grafana dashboards. The headline series is
``echo_sre_llm_fallbacks_total`` — it proves the automatic provider failover at runtime.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram, start_http_server

LLM_REQUESTS = Counter(
    "echo_sre_llm_requests_total",
    "LLM chat requests by provider/model and outcome.",
    ["provider", "model", "outcome"],  # outcome: success|retryable_error|fatal_error
)

LLM_LATENCY = Histogram(
    "echo_sre_llm_request_latency_seconds",
    "LLM chat request latency in seconds, by provider.",
    ["provider"],
)

LLM_TOKENS = Counter(
    "echo_sre_llm_tokens_total",
    "Tokens consumed, by provider and kind.",
    ["provider", "kind"],  # kind: prompt|completion
)

LLM_FALLBACKS = Counter(
    "echo_sre_llm_fallbacks_total",
    "Automatic failovers from one provider to the next.",
    ["from_provider", "to_provider"],
)

CONTEXT_TRIMS = Counter(
    "echo_sre_context_trims_total",
    "Times the context was trimmed to fit a provider's window.",
)

AGENT_STEPS = Counter(
    "echo_sre_agent_steps_total",
    "Agent reasoning steps by outcome.",
    ["outcome"],  # tool_call|final|max_steps
)

AGENT_TOOL_CALLS = Counter(
    "echo_sre_agent_tool_calls_total",
    "Tool calls issued by the agent, by tool name.",
    ["tool"],
)

_metrics_started = False


def start_metrics_server(port: int) -> None:
    """Start the Prometheus exporter once per process (idempotent, best-effort)."""
    global _metrics_started
    if _metrics_started:
        return
    try:
        start_http_server(port)
        _metrics_started = True
    except OSError:
        # Port already bound (e.g. reloader / second worker) — safe to ignore.
        _metrics_started = True
