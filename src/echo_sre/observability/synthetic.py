"""Zero-dependency synthetic observability backend.

Serves deterministic metrics/logs/alerts/topology from a scenario dict so the whole
agentic investigation runs with no external services. The scenario injects a *real*
causal chain (e.g. checkout latency -> payments timeouts -> postgres connection
exhaustion) so the agent has something genuine to root-cause.
"""

from __future__ import annotations

import re
import time

from .base import Alert, LogLine, MetricSeries, ServiceNode

_LABEL_RE = re.compile(r'(\w+)\s*=\s*"?([^",}]+)"?')

# Bundled default so the MCP server / backend works even without a scenario file.
DEFAULT_SCENARIO: dict = {
    "title": "Checkout p95 latency 5x",
    "alert": "ALERT HighLatencyCheckout: checkout p95 latency is 5x baseline for 10m",
    "topology": [
        {"name": "gateway", "depends_on": ["checkout"]},
        {"name": "checkout", "depends_on": ["payments", "orders"]},
        {"name": "payments", "depends_on": ["postgres"]},
        {"name": "orders", "depends_on": ["postgres", "redis"]},
        {"name": "postgres", "depends_on": []},
        {"name": "redis", "depends_on": []},
    ],
    "metrics": [
        {"metric": "http_request_duration_p95", "labels": {"service": "checkout"},
         "baseline": 0.2, "anomaly": 1.0, "anomaly_start_min": -12, "unit": "s"},
        {"metric": "http_requests_error_rate", "labels": {"service": "payments"},
         "baseline": 0.01, "anomaly": 0.18, "anomaly_start_min": -12, "unit": "ratio"},
        {"metric": "pg_connections_active", "labels": {"service": "postgres"},
         "baseline": 40, "anomaly": 100, "anomaly_start_min": -13, "unit": "count"},
        {"metric": "http_request_duration_p95", "labels": {"service": "orders"},
         "baseline": 0.15, "anomaly": 0.16, "anomaly_start_min": 0, "unit": "s"},
    ],
    "logs": [
        {"service": "payments", "level": "error", "message": "db timeout acquiring connection from pool (waited 5000ms)"},
        {"service": "payments", "level": "error", "message": "upstream request failed: context deadline exceeded"},
        {"service": "postgres", "level": "warning", "message": "FATAL: too many connections for role \"payments\""},
        {"service": "checkout", "level": "warning", "message": "downstream payments p99 elevated; retrying"},
        {"service": "gateway", "level": "info", "message": "200 POST /checkout 5123ms"},
    ],
    "alerts": [
        {"name": "HighLatencyCheckout", "severity": "critical", "service": "checkout",
         "summary": "checkout p95 latency 5x baseline for 10m"},
        {"name": "PaymentErrorsHigh", "severity": "warning", "service": "payments",
         "summary": "payments error rate > 15%"},
    ],
    "root_cause_key": "postgres-connection-exhaustion",
}


def _parse_labels(query: str) -> dict[str, str]:
    inside = query[query.find("{") + 1 : query.rfind("}")] if "{" in query else ""
    return {k: v.strip() for k, v in _LABEL_RE.findall(inside)}


def _metric_name(query: str) -> str:
    return re.split(r"[{(\s]", query.strip(), maxsplit=1)[0].strip()


class SyntheticBackend:
    def __init__(self, scenario: dict | None = None):
        self.scenario = scenario or DEFAULT_SCENARIO

    # -- metrics ---------------------------------------------------------------
    def _series(self, spec: dict, minutes: int) -> MetricSeries:
        now = time.time()
        baseline = float(spec.get("baseline", 1.0))
        anomaly = float(spec.get("anomaly", baseline))
        start_min = int(spec.get("anomaly_start_min", 0))
        points: list[tuple[float, float]] = []
        for i, t_min in enumerate(range(-minutes, 1)):
            value = anomaly if t_min >= start_min and start_min != 0 else baseline
            if anomaly == baseline:  # stable series get a tiny deterministic ripple
                value = baseline * (1 + 0.01 * ((i % 4) - 1.5))
            points.append((now + t_min * 60, round(value, 4)))
        return MetricSeries(metric=spec["metric"], labels=spec.get("labels", {}), points=points)

    async def query_metrics(self, query: str, minutes: int = 30) -> list[MetricSeries]:
        name = _metric_name(query).lower()
        want_labels = _parse_labels(query)
        out: list[MetricSeries] = []
        for spec in self.scenario.get("metrics", []):
            m = spec["metric"].lower()
            name_ok = (not name) or name in m or m in (query.lower())
            labels_ok = all(spec.get("labels", {}).get(k) == v for k, v in want_labels.items())
            if name_ok and labels_ok:
                out.append(self._series(spec, minutes))
        # If nothing matched the (possibly fuzzy) query, return everything so the agent
        # still gets signal rather than an empty result.
        if not out:
            out = [self._series(s, minutes) for s in self.scenario.get("metrics", [])]
        return out

    # -- logs ------------------------------------------------------------------
    async def search_logs(
        self, service=None, pattern=None, minutes: int = 30, limit: int = 100
    ) -> list[LogLine]:
        now = time.time()
        out: list[LogLine] = []
        for i, log in enumerate(self.scenario.get("logs", [])):
            if service and log.get("service") != service:
                continue
            if pattern and pattern.lower() not in log.get("message", "").lower():
                continue
            out.append(
                LogLine(
                    ts=now - (len(self.scenario.get("logs", [])) - i) * 30,
                    service=log["service"],
                    level=log.get("level", "info"),
                    message=log["message"],
                )
            )
        return out[:limit]

    # -- alerts ----------------------------------------------------------------
    async def list_alerts(self, active_only: bool = True) -> list[Alert]:
        now = time.time()
        return [
            Alert(
                name=a["name"], severity=a.get("severity", "warning"), service=a.get("service", ""),
                summary=a.get("summary", ""), since=now - 600, labels=a.get("labels", {}),
            )
            for a in self.scenario.get("alerts", [])
        ]

    # -- topology --------------------------------------------------------------
    async def service_topology(self) -> list[ServiceNode]:
        return [
            ServiceNode(name=n["name"], depends_on=n.get("depends_on", []))
            for n in self.scenario.get("topology", [])
        ]
