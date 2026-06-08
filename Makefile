.PHONY: install demo test serve-api serve-mcp providers metrics fmt

install:
	pip install -e ".[dev]"

# Run the full agentic investigation on the bundled synthetic incident,
# offline, with no API keys (uses the deterministic mock model).
demo:
	echo-sre demo

# Investigate an alert with the real inference gateway (needs a provider key or local vLLM).
investigate:
	echo-sre investigate "checkout p95 latency is 5x baseline"

# Launch the FastAPI streaming server (the deployable Azure service).
serve-api:
	echo-sre serve-api --host 0.0.0.0 --port 8000

# Launch the standalone MCP server for Claude Desktop / Cursor (stdio).
serve-mcp:
	echo-sre serve-mcp

# Show the resolved provider chain and which providers have usable keys.
providers:
	echo-sre providers

test:
	pytest -q

# Scrape the Prometheus metrics exporter (requires a running CLI/API process).
metrics:
	curl -s localhost:9090/metrics | grep echo_sre_
