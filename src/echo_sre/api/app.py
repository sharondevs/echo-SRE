"""FastAPI app exposing the SRE agent as a streaming endpoint.

Wire-compatible with the existing ECHO chat backend so the hosted portfolio can add an
"SRE mode" with no client rewrite: POST /stream-chat returns Server-Sent Events whose
``data:`` payloads are ``{text?, sources?, metadata?, error?}`` JSON objects.

The agent's live investigation (each tool call + evidence) is streamed as Markdown so it
renders as a readable trace in the portfolio, followed by the structured root-cause verdict.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import __version__
from ..agent.loop import AgentEvent, AgentRunner
from ..config import get_settings
from ..inference import InferenceGateway
from ..inference.metrics import start_metrics_server
from ..observability.factory import load_scenario


class ChatRequest(BaseModel):
    message: str
    mode: str | None = "sre"
    session_id: str | None = None
    scenario: str | None = None  # optional scenario file path (synthetic backend)
    max_steps: int | None = None


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _render_event(ev: AgentEvent) -> dict | None:
    """Map an AgentEvent to an ECHO-style SSE chunk ({text}/{sources}/{error})."""
    if ev.type == "status":
        return {"text": "**🔍 ECHO-SRE investigating the incident…**\n\n"}
    if ev.type == "tool_call":
        return {"text": f"- 🔧 `{ev.text}`\n"}
    if ev.type == "tool_result":
        return {"text": f"  - ↳ {ev.text}\n"}
    if ev.type == "token":
        return {"text": ev.text}
    if ev.type == "final":
        # Insert a divider before the verdict was already streamed via tokens; here we
        # only surface the citation sources.
        return {"sources": ev.sources or []}
    if ev.type == "error":
        return {"error": ev.text}
    return None  # 'done' handled by the generator's closing metadata


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ECHO-SRE", version=__version__)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Build the gateway once (provider chain + automatic fallback) and reuse it.
    gateway = InferenceGateway.from_config(settings.providers_file)
    start_metrics_server(settings.metrics_port)

    @app.get("/")
    async def health() -> dict:
        return {
            "service": "echo-sre",
            "version": __version__,
            "status": "ok",
            "backend": settings.backend,
            "providers": gateway.provider_names,
        }

    @app.get("/scenarios")
    async def scenarios() -> dict:
        sc = load_scenario(None)
        return {"default": {"title": sc.get("title"), "alert": sc.get("alert")}}

    # Session endpoints kept for parity with the ECHO client (the SRE agent is stateless).
    @app.delete("/session/{session_id}")
    async def cleanup(session_id: str) -> dict:
        return {"message": "ok", "session_id": session_id}

    @app.post("/stream-chat")
    async def stream_chat(req: ChatRequest) -> StreamingResponse:
        runner = AgentRunner(
            gateway,
            max_steps=req.max_steps or 8,
            use_mcp=False,  # direct in-process tools for throughput under load
            scenario=load_scenario(req.scenario),
        )
        model_used = gateway.provider_names[0] if gateway.provider_names else "mock"

        async def event_stream() -> AsyncIterator[str]:
            try:
                last_sources: list[str] = []
                divider_sent = False
                async for ev in runner.stream(req.message):
                    if ev.type == "token" and not divider_sent:
                        # Separate the live trace from the final verdict once tokens begin.
                        divider_sent = True
                        yield _sse({"text": "\n\n---\n\n"})
                    if ev.type == "final":
                        last_sources = ev.sources or []
                    chunk = _render_event(ev)
                    if chunk is not None:
                        yield _sse(chunk)
                yield _sse(
                    {
                        "metadata": {
                            "session_id": req.session_id or "sre",
                            "query_type": "sre",
                            "model_used": model_used,
                            "sources": last_sources,
                        }
                    }
                )
            except Exception as exc:  # noqa: BLE001
                yield _sse({"error": str(exc)})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
