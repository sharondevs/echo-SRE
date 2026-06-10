"""ECHO-SRE command line.

  echo-sre demo                      # offline, deterministic agentic investigation (no keys)
  echo-sre investigate "<alert>"     # real run via the inference gateway (vLLM / Gemini / OpenAI)
  echo-sre serve-api                 # the deployable FastAPI streaming service
  echo-sre serve-mcp                 # standalone MCP server for Claude Desktop / Cursor
  echo-sre providers                 # show the resolved provider chain + key availability
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .agent.loop import AgentRunner
from .config import get_settings, load_providers
from .inference import InferenceGateway
from .inference.metrics import start_metrics_server
from .inference.mock import MockProvider
from .observability.factory import load_scenario

app = typer.Typer(add_completion=True, help="ECHO-SRE — agentic SRE copilot over MCP.")
console = Console()


async def _drive(runner: AgentRunner, alert: str) -> None:
    """Render the streaming investigation to the terminal."""
    console.print(Panel(alert, title="🚨 incident", border_style="red"))
    verdict: list[str] = []
    in_verdict = False
    for_sources: list[str] = []
    async for ev in runner.stream(alert):
        if ev.type == "tool_call":
            console.print(f"[bold cyan]🔧 {ev.text}[/bold cyan]")
        elif ev.type == "tool_result":
            console.print(f"   [dim]↳ {ev.text}[/dim]")
        elif ev.type == "token":
            in_verdict = True
            verdict.append(ev.text)
        elif ev.type == "final":
            for_sources = ev.sources or []
        elif ev.type == "error":
            console.print(f"[bold red]error:[/bold red] {ev.text}")
    if in_verdict:
        console.print(Panel("".join(verdict).strip(), title="🧭 verdict", border_style="green"))
    if for_sources:
        console.print("[bold]sources:[/bold] " + ", ".join(for_sources))


@app.command()
def demo():
    """Run the bundled incident with the offline mock model — no API keys required."""
    start_metrics_server(get_settings().metrics_port)
    gateway = InferenceGateway([MockProvider()])
    scenario = load_scenario("scenarios/checkout_latency.json")
    runner = AgentRunner(
        gateway, scenario=scenario, backend_env={"ECHO_SRE_BACKEND": "synthetic"}
    )
    console.print("[dim]model: mock (offline)  •  tools: MCP[/dim]")
    asyncio.run(_drive(runner, scenario["alert"]))


@app.command()
def investigate(
    alert: str = typer.Argument(..., help="The alert / symptom to investigate."),
    scenario: str = typer.Option("scenarios/checkout_latency.json", help="Synthetic scenario file."),
    max_steps: int = typer.Option(8, help="Max agent reasoning steps."),
):
    """Investigate an alert with the real inference gateway (provider chain + fallback)."""
    start_metrics_server(get_settings().metrics_port)
    gateway = InferenceGateway.from_config()
    console.print("[dim]provider chain: %s[/dim]" % " → ".join(gateway.provider_names))
    runner = AgentRunner(
        gateway, max_steps=max_steps, scenario=load_scenario(scenario),
        backend_env={"ECHO_SRE_BACKEND": "synthetic"},
    )
    asyncio.run(_drive(runner, alert))


@app.command("serve-api")
def serve_api(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
):
    """Launch the FastAPI SSE streaming service (the deployable Azure container)."""
    from .api.app import serve

    serve(host=host, port=port)


@app.command("serve-mcp")
def serve_mcp(
    http: bool = typer.Option(False, "--http", help="Serve over streamable HTTP instead of stdio."),
):
    """Run the standalone MCP server for Claude Desktop / Cursor / any MCP client."""
    import os

    os.environ["ECHO_SRE_MCP_TRANSPORT"] = "streamable-http" if http else "stdio"
    from .mcp_server.server import main

    main()


@app.command()
def providers():
    """Show the resolved provider chain and which providers have usable keys."""
    cfgs, _ = load_providers()
    table = Table(title="ECHO-SRE inference provider chain (priority order)")
    table.add_column("#", justify="right")
    table.add_column("provider")
    table.add_column("model")
    table.add_column("base_url")
    table.add_column("usable", justify="center")
    usable_any = False
    for i, c in enumerate(cfgs, 1):
        ok = c.is_usable
        usable_any = usable_any or ok
        table.add_row(str(i), c.name, c.model, c.base_url, "[green]✓[/green]" if ok else "[red]✗[/red]")
    console.print(table)
    if not usable_any:
        console.print("[yellow]No provider has a usable key — runs will use the offline mock model.[/yellow]")


if __name__ == "__main__":
    app()
