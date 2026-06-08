"""Backend-neutral observability data model + the backend protocol.

The synthetic demo backend and the real Prometheus/Loki backend implement the same
interface, so the MCP tools and the agent are completely backend-agnostic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class MetricSeries(BaseModel):
    metric: str
    labels: dict[str, str] = Field(default_factory=dict)
    points: list[tuple[float, float]] = Field(default_factory=list)  # (unix_ts, value)

    def latest(self) -> float | None:
        return self.points[-1][1] if self.points else None

    def summary(self) -> dict:
        """Compact, model-friendly view (avoids dumping every raw point)."""
        vals = [v for _, v in self.points]
        return {
            "metric": self.metric,
            "labels": self.labels,
            "latest": round(vals[-1], 4) if vals else None,
            "min": round(min(vals), 4) if vals else None,
            "max": round(max(vals), 4) if vals else None,
            "avg": round(sum(vals) / len(vals), 4) if vals else None,
            "points": len(vals),
        }


class LogLine(BaseModel):
    ts: float
    service: str
    level: str
    message: str


class Alert(BaseModel):
    name: str
    severity: str
    service: str
    summary: str
    since: float = 0.0
    labels: dict[str, str] = Field(default_factory=dict)


class ServiceNode(BaseModel):
    name: str
    depends_on: list[str] = Field(default_factory=list)


@runtime_checkable
class ObservabilityBackend(Protocol):
    async def query_metrics(self, query: str, minutes: int = 30) -> list[MetricSeries]: ...

    async def search_logs(
        self,
        service: str | None = None,
        pattern: str | None = None,
        minutes: int = 30,
        limit: int = 100,
    ) -> list[LogLine]: ...

    async def list_alerts(self, active_only: bool = True) -> list[Alert]: ...

    async def service_topology(self) -> list[ServiceNode]: ...
