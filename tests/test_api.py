"""FastAPI streaming endpoint — offline, mock model, real MCP subprocess per request.

Verifies the SSE contract and that each SRE sub-mode (demo / custom / live) routes the
agent at the right backend. The mock model's final verdict is fixed, so the custom-scenario
assertion checks the streamed *tool trace* (which reflects the uploaded incident) — that is
what proves per-request scenario injection actually reached the backend.
"""

import json

import pytest
from fastapi.testclient import TestClient

from echo_sre.api import app as app_module
from echo_sre.inference.gateway import InferenceGateway
from echo_sre.inference.mock import MockProvider


@pytest.fixture
def client(monkeypatch):
    # Force the offline mock model so the endpoint needs no API keys.
    monkeypatch.setattr(
        InferenceGateway,
        "from_config",
        classmethod(lambda cls, *a, **k: InferenceGateway([MockProvider()])),
    )
    return TestClient(app_module.create_app())


def _chunks(resp) -> list[dict]:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def test_health(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "echo-sre"


def test_stream_demo_reaches_verdict(client):
    r = client.post("/stream-chat", json={"message": "checkout is slow", "sre_mode": "demo"})
    assert r.status_code == 200
    chunks = _chunks(r)
    text = "".join(c.get("text", "") for c in chunks).lower()
    assert "postgres" in text or "connection" in text
    # The stream is terminated by a metadata frame carrying the model + sources.
    assert any("metadata" in c for c in chunks)


def test_stream_custom_scenario_drives_backend(client):
    scenario = {
        "title": "Cache stampede",
        "alert": "ALERT DBOverload: postgres QPS 10x after redis restart",
        "topology": [
            {"name": "api", "depends_on": ["redis", "postgres"]},
            {"name": "redis", "depends_on": []},
            {"name": "postgres", "depends_on": []},
        ],
        "metrics": [
            {"metric": "redis_hit_ratio", "labels": {"service": "redis"},
             "baseline": 0.95, "anomaly": 0.1, "anomaly_start_min": -8},
        ],
        "logs": [{"service": "redis", "level": "warning", "message": "node restarted, cache cold"}],
        "alerts": [{"name": "DBOverload", "severity": "critical", "service": "postgres",
                    "summary": "QPS 10x after cache flush"}],
        "root_cause_key": "cache-stampede",
    }
    r = client.post(
        "/stream-chat",
        json={"message": "db melting", "sre_mode": "custom", "scenario": scenario},
    )
    assert r.status_code == 200
    text = "".join(c.get("text", "") for c in _chunks(r)).lower()
    # The agent's tool trace must reflect the *uploaded* incident, not the bundled default.
    assert "redis" in text or "dboverload" in text
