"""Configuration: app settings + the data-driven inference provider registry.

The provider registry is loaded from a YAML file so adding a local vLLM server or a
new OpenAI-compatible API is config-only — no code changes. A provider is considered
"usable" if it resolves an API key, or if it points at a local base_url (vLLM accepts
any token). If no provider is usable, callers fall back to the offline mock model.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load a local .env (if present) before reading settings, mirroring the ECHO backend.
try:  # optional dependency path; python-dotenv may not be installed
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is convenience only
    if Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


class ProviderConfig(BaseModel):
    """One inference backend in the priority-ordered registry."""

    name: str
    base_url: str
    api_key_env: str | None = None
    model: str
    timeout_s: float = 45
    max_context_tokens: int = 8192
    enabled: bool = True

    def resolve_api_key(self) -> str:
        """Return the configured key, or 'EMPTY' (vLLM tolerates any token)."""
        if self.api_key_env:
            return os.environ.get(self.api_key_env) or "EMPTY"
        return "EMPTY"

    @property
    def is_local(self) -> bool:
        return any(h in self.base_url for h in _LOCAL_HOSTS)

    @property
    def is_usable(self) -> bool:
        """A real key is present, or it's a local server we can always try."""
        if self.is_local:
            return True
        return bool(self.api_key_env and os.environ.get(self.api_key_env))


class GatewayDefaults(BaseModel):
    temperature: float = 0.1
    max_tokens: int = 1024
    retries_per_provider: int = 2
    backoff_base_s: float = 0.5


class Settings(BaseSettings):
    """Process-level configuration, overridable via ECHO_SRE_* env vars."""

    model_config = SettingsConfigDict(env_prefix="ECHO_SRE_", extra="ignore")

    providers_file: str = "config/providers.yaml"
    backend: str = "synthetic"  # synthetic | prometheus
    use_mcp: bool = True
    metrics_port: int = 9090
    log_level: str = "INFO"
    cors_origins: str = "https://hello-sharon.dev,http://localhost:5173"

    # Production observability backend (used when backend == "prometheus")
    prom_url: str = ""
    loki_url: str = ""
    alertmanager_url: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def get_settings() -> Settings:
    return Settings()


def load_providers(
    path: str | None = None,
) -> tuple[list[ProviderConfig], GatewayDefaults]:
    """Load enabled providers (priority order preserved) and gateway defaults.

    Falls back to the bundled example file if the configured path is missing, so a
    fresh checkout runs without copying files first. Returns ([], defaults) when no
    config exists at all — the gateway then uses the offline mock model.
    """
    candidates = [path or get_settings().providers_file, "config/providers.example.yaml"]
    chosen: Path | None = next((Path(c) for c in candidates if c and Path(c).exists()), None)
    if chosen is None:
        return [], GatewayDefaults()

    data = yaml.safe_load(chosen.read_text()) or {}
    providers = [ProviderConfig(**p) for p in data.get("providers", []) if p.get("enabled", True)]
    defaults = GatewayDefaults(**(data.get("defaults") or {}))
    return providers, defaults
