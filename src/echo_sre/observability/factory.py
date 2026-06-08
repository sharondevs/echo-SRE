"""Backend selection: synthetic demo data, or a real Prometheus/Loki stack."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings, get_settings
from .base import ObservabilityBackend
from .prometheus import PromBackend
from .synthetic import DEFAULT_SCENARIO, SyntheticBackend


def load_scenario(path: str | None) -> dict:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text())
    return DEFAULT_SCENARIO


def build_backend(
    settings: Settings | None = None, scenario: dict | None = None
) -> ObservabilityBackend:
    settings = settings or get_settings()
    if settings.backend == "prometheus" and settings.prom_url:
        return PromBackend(
            prom_url=settings.prom_url,
            loki_url=settings.loki_url or None,
            alertmanager_url=settings.alertmanager_url or None,
        )
    return SyntheticBackend(scenario or DEFAULT_SCENARIO)
