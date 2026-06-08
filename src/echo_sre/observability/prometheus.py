"""Production observability backend: Prometheus (+ Loki, + Alertmanager).

Implements the same :class:`ObservabilityBackend` protocol as the synthetic backend, so
switching from demo to real telemetry is a config flag (``ECHO_SRE_BACKEND=prometheus``).
"""

from __future__ import annotations

import time

import httpx

from .base import Alert, LogLine, MetricSeries, ServiceNode


class PromBackend:
    def __init__(
        self,
        prom_url: str,
        loki_url: str | None = None,
        alertmanager_url: str | None = None,
        topology: list[dict] | None = None,
        timeout_s: float = 15.0,
    ):
        self.prom_url = prom_url.rstrip("/")
        self.loki_url = (loki_url or "").rstrip("/")
        self.alertmanager_url = (alertmanager_url or "").rstrip("/")
        self._topology = topology or []
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def query_metrics(self, query: str, minutes: int = 30) -> list[MetricSeries]:
        end = time.time()
        start = end - minutes * 60
        step = max(15, (minutes * 60) // 60)  # ~60 points
        r = await self._client.get(
            f"{self.prom_url}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
        )
        r.raise_for_status()
        result = r.json().get("data", {}).get("result", [])
        series: list[MetricSeries] = []
        for item in result:
            labels = item.get("metric", {})
            metric = labels.pop("__name__", query)
            points = [(float(ts), float(val)) for ts, val in item.get("values", [])]
            series.append(MetricSeries(metric=metric, labels=labels, points=points))
        return series

    async def search_logs(
        self, service=None, pattern=None, minutes: int = 30, limit: int = 100
    ) -> list[LogLine]:
        if not self.loki_url:
            return []
        selector = f'{{service="{service}"}}' if service else "{job=~\".+\"}"
        logql = f'{selector} |= "{pattern}"' if pattern else selector
        end = int(time.time() * 1e9)
        start = end - minutes * 60 * int(1e9)
        r = await self._client.get(
            f"{self.loki_url}/loki/api/v1/query_range",
            params={"query": logql, "start": start, "end": end, "limit": limit},
        )
        r.raise_for_status()
        out: list[LogLine] = []
        for stream in r.json().get("data", {}).get("result", []):
            svc = stream.get("stream", {}).get("service", service or "")
            level = stream.get("stream", {}).get("level", "info")
            for ts_ns, line in stream.get("values", []):
                out.append(LogLine(ts=int(ts_ns) / 1e9, service=svc, level=level, message=line))
        out.sort(key=lambda x: x.ts)
        return out[:limit]

    async def list_alerts(self, active_only: bool = True) -> list[Alert]:
        base = self.alertmanager_url or self.prom_url
        path = "/api/v2/alerts" if self.alertmanager_url else "/api/v1/alerts"
        r = await self._client.get(f"{base}{path}")
        r.raise_for_status()
        data = r.json()
        raw = data.get("data", {}).get("alerts", data) if isinstance(data, dict) else data
        out: list[Alert] = []
        for a in raw or []:
            labels = a.get("labels", {})
            ann = a.get("annotations", {})
            if active_only and a.get("state", "firing") not in ("firing", "active"):
                continue
            out.append(
                Alert(
                    name=labels.get("alertname", "alert"),
                    severity=labels.get("severity", "warning"),
                    service=labels.get("service", labels.get("job", "")),
                    summary=ann.get("summary", ann.get("description", "")),
                    labels=labels,
                )
            )
        return out

    async def service_topology(self) -> list[ServiceNode]:
        return [ServiceNode(name=n["name"], depends_on=n.get("depends_on", [])) for n in self._topology]

    async def aclose(self) -> None:
        await self._client.aclose()
