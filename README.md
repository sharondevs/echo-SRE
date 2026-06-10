# ECHO-SRE — Agentic SRE Copilot over MCP

> An LLM agent that investigates production incidents **end-to-end over the Model Context
> Protocol**, powered by a **provider-agnostic inference gateway** that runs on local
> **vLLM** or any **OpenAI-compatible API** (Gemini / OpenAI / Groq) with **automatic
> failover**. Ships with a zero-dependency demo, a real MCP server, a streaming HTTP API,
> Prometheus metrics, and a one-click Azure deploy.

[![tests](https://img.shields.io/badge/tests-passing-brightgreen)](#testing)
[![mcp](https://img.shields.io/badge/MCP-server%20%2B%20client-blue)](#mcp)
[![inference](https://img.shields.io/badge/inference-vLLM%20%7C%20OpenAI--compatible-orange)](#the-inference-gateway)

Part of the **ECHO** family (sibling to the [ECHO chat assistant](https://hello-sharon.dev)).
The portfolio's chat UI gains an **SRE mode** that streams from this service.

---

## Why this exists

In 2026 MCP is the de-facto standard for connecting agents to systems, and the durable value
is in the **server architecture** — reusable, observable, scalable connectivity — not the
model. ECHO-SRE leans into that: the model is a swappable commodity behind a gateway, while
the MCP toolset and the agent loop are the product. The result demonstrates **AI at scale**
on two axes:

- **Inference scale** — one client, many backends; vLLM batching for self-hosted throughput,
  hosted APIs for burst, and **automatic fallback** across a multi-model chain.
- **Systems scale** — a stateless MCP server + a streaming API that any client (Claude
  Desktop, Cursor, a web UI) can drive, fully instrumented with Prometheus.

## Architecture

```
   Claude Desktop / Cursor ─┐                         ┌─ query_metrics
   Portfolio "SRE mode"  ───┤   (MCP / SSE)           ├─ search_logs
   CLI  ────────────────────┤                         ├─ list_alerts
                            ▼                          ├─ get_service_topology
                  ┌───────────────────┐   MCP tools   ├─ get_runbook (BM25 RAG)
                  │   SRE Agent loop   │──────────────►│  ECHO-SRE MCP server
                  │  (ReAct, budgeted) │               └───────┬──────────────┘
                  └─────────┬─────────┘                        │
                            │ chat + tools          ┌──────────▼───────────┐
                  ┌─────────▼──────────┐             │ Observability backend │
                  │ Inference Gateway  │             │ synthetic (demo) or   │
                  │ priority routing + │             │ Prometheus + Loki     │
                  │ AUTOMATIC FALLBACK │             └───────────────────────┘
                  │ + token budgeting  │
                  └─────────┬──────────┘
            ┌───────────────┼───────────────┬───────────────┐
       local vLLM       Gemini           OpenAI           Groq …
   (OpenAI-compatible — swap base_url / model / key; same code path)
```

## Quickstart (no API key required)

```bash
make install          # pip install -e ".[dev]"
echo-sre demo         # full agentic investigation, offline, over a real MCP round-trip
```

`demo` uses a deterministic **mock model** and a **synthetic incident** (checkout p95
latency → payments timeouts → postgres connection-pool exhaustion), so it runs anywhere with
zero external services. The agent pulls alerts/metrics/topology/logs, consults a runbook, and
prints a structured **Root Cause / Evidence / Remediation / Confidence** verdict.

## Use a real model

Copy the configs and add a key (or point at a local vLLM):

```bash
cp .env.example .env                       # add GEMINI_API_KEY=...
cp config/providers.example.yaml config/providers.yaml
echo-sre providers                         # show the resolved chain + which keys are live
echo-sre investigate "checkout p95 latency is 5x baseline"
```

**Local vLLM** (self-hosted, high-throughput): enable the `vllm-local` provider in
`config/providers.yaml` and serve a tool-calling model:

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --enable-auto-tool-choice --tool-call-parser llama3_json
```

### The inference gateway

The gateway tries providers in priority order and **fails over automatically** on
timeout / rate-limit / 5xx, with per-provider retries + backoff and per-provider context
budgeting. Because every provider speaks the OpenAI Chat Completions API, vLLM and hosted
APIs are interchangeable links in one chain. The default config stacks several Gemini models
(all verified for OpenAI-format tool calling) so the **aggregate daily quota is the sum of
each model's** — and a 503 on one transparently rolls to the next.

> Gemini 3.x note: those "thinking" models return a `thought_signature` that must be replayed
> on later turns or multi-turn tool calls 400. ECHO-SRE captures and round-trips it
> automatically (only for the Gemini endpoint), so 3.x models work in the agent loop.

## MCP

ECHO-SRE is a real MCP server, not just an LLM with functions. Run it standalone and connect
any MCP client:

```bash
echo-sre serve-mcp            # stdio (default)
echo-sre serve-mcp --http     # streamable HTTP
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "echo-sre": { "command": "echo-sre", "args": ["serve-mcp"] }
  }
}
```

Tools exposed: `query_metrics`, `search_logs`, `list_alerts`, `get_service_topology`,
`get_runbook`. The agent consumes these *through a genuine MCP `ClientSession`* (with a fast
in-process path for tests/the API).

## Streaming API (the deployable service)

```bash
echo-sre serve-api            # uvicorn on :8000
```

`POST /stream-chat` returns Server-Sent Events wire-compatible with the ECHO chat backend
(`data: {text? | sources? | metadata? | error?}`), streaming the live investigation trace
followed by the verdict. `GET /` is a health/info probe; the Prometheus exporter on port
9090 exposes series including **`echo_sre_llm_fallbacks_total`**.

The request carries an `sre_mode` selecting where the agent sources telemetry — each run
spins up its own MCP server with that backend, so a single hosted service serves all three:

- `demo` (default) — the bundled synthetic incident; no config.
- `custom` — a caller-supplied synthetic scenario JSON (`scenario`).
- `live` — a real stack (`prometheus_url`, optional `loki_url` / `alertmanager_url`).

```bash
# demo
curl -N -X POST localhost:8000/stream-chat -H 'Content-Type: application/json' \
  -d '{"message":"checkout p95 latency is 5x baseline","sre_mode":"demo"}'

# live (bring your own Prometheus/Loki)
curl -N -X POST localhost:8000/stream-chat -H 'Content-Type: application/json' \
  -d '{"message":"investigate the firing alerts","sre_mode":"live",
       "prometheus_url":"https://prom.mycorp.io","loki_url":"https://loki.mycorp.io"}'
```

## Real telemetry instead of the demo

Set `ECHO_SRE_BACKEND=prometheus` and `ECHO_SRE_PROM_URL` / `ECHO_SRE_LOKI_URL`
/ `ECHO_SRE_ALERTMANAGER_URL`. The agent and MCP tools are backend-agnostic — nothing else
changes.

## Deploy (Azure Container Apps)

`Dockerfile` + `.github/workflows/deploy.yml` build and deploy on push to `main` (after the
test gate). Configure:

- **Secrets:** `AZURE_CREDENTIALS` (service-principal JSON), `GEMINI_API_KEY`.
- **Variables (optional):** `AZURE_ACR_NAME` (required), `AZURE_RESOURCE_GROUP`,
  `AZURE_CONTAINERAPP`, `AZURE_CONTAINERAPP_ENV`, `AZURE_LOCATION`.

The container ships the example provider registry and reads real keys from env, so it
degrades gracefully to the mock model if no key is present.

## Testing

```bash
pytest -q   # offline: gateway fallback, token budgeting, provider/tool serialization, full agent loop
```

## Project layout

```
src/echo_sre/
  inference/     gateway, provider, mock, budget, metrics, types   ← the scalable spine
  observability/ base, synthetic (demo), prometheus (prod), factory
  rag/           BM25 runbook retrieval + runbooks/*.md
  mcp_server/    FastMCP server (the tools)
  agent/         ReAct loop (MCP + direct) + SRE prompt
  api/           FastAPI SSE streaming service
  tools.py       canonical tool registry (shared by MCP server + agent)
  cli.py         demo / investigate / serve-api / serve-mcp / providers
```

## Roadmap

Grafana dashboards + Docker-Compose Prometheus/Loki stack, embedding-based runbook RAG,
OAuth/multi-tenant hardening, an incident-timeline web UI, and an evaluation harness.

---

MIT licensed. Built by [Sharon Dev Saseendran](https://hello-sharon.dev).
