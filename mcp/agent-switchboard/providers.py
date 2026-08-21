"""providers.py — declarative execution-layer (provider) management for Agent Switchboard.

Switchboard's purpose is to let a "decision-layer" AI call "execution layers"
(cheap/specialised: local CLIs, third-party relays, local servers) without drifting
off-goal and while saving tokens. This module adds ``providers`` as a first-class
config object so the decision layer can:

  * discover which execution layers exist and their models (via ``list_providers``),
  * see whether each is REALLY usable (official CLI logged in? relay reachable?),
  * route to any one of them explicitly via ``route_agent_task(target_agent=...)``.

Two provider source types:
  * ``official_cli``: a locally-installed, logged-in official CLI
    (claude / codex / gemini). Reuses the existing built-in backend logic.
  * ``openai_compat``: any OpenAI-compatible relay or local server
    (base_url + optional api_key + models). Reuses GenericCliBackend's HTTP channel.

No built-in routing priority: the caller says which provider/model to use each time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Per-CLI credential probe: exact known absolute credential paths + env vars.
# Checked at discovery time only; never recursive-scan the home directory.
# Tuple = (absolute_path_under_home_as "filename" or full relative, OR "env:NAME", is_env_marker)
_CRED_PROBES: dict[str, list[tuple[str, bool]]] = {
    # cli -> [(path_or_env, is_env_marker)]
    "claude": [
        (".claude/.credentials.json", False),
        ("env:ANTHROPIC_API_KEY", True),
    ],
    "codex": [
        (".codex/auth.json", False),
    ],
    "gemini": [
        (".gemini/settings.json", False),
    ],
}

# CLIs that Google retired / no longer support (gemini CLI -> migrate to Antigravity).
# auth_available() reports them unavailable and retirement_reason() explains why.
_RETIRED_CLIS: dict[str, str] = {
    "gemini": "retired: Google no longer supports 'Gemini Code Assist for individuals' "
              "(IneligibleTierError); use the antigravity_cli (agy) execution layer instead.",
}


def retirement_reason(cli: str | None) -> str | None:
    """Return a human-readable retirement reason for a retired CLI, or None."""
    if not cli:
        return None
    return _RETIRED_CLIS.get(str(cli).strip().lower())


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _file_probe_present(rel_path: str) -> bool:
    """Check a known credential file (relative to the user home) exists and is non-empty."""
    rel = str(rel_path).replace("/", os.sep).lstrip(os.sep)
    if not rel:
        return False
    path = _home() / rel
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _env_probe_present(key: str) -> bool:
    if not key:
        return False
    value = os.environ.get(key.strip())
    return bool(value and str(value).strip())


def auth_available(cli: str | None) -> bool:
    """Whether an official CLI appears logged in / ready (credential probe only).

    Dependency-free: checks known credential files / env vars, never runs the CLI and
    never recursively scans the home directory. Returns True when no probe is defined
    for the CLI (fail-open, same as PATH-only). Retired CLIs (see ``_RETIRED_CLIS``)
    always report unavailable.
    """
    if not cli:
        return False
    cli_key = str(cli).strip().lower()
    if cli_key in _RETIRED_CLIS:
        return False
    probes = _CRED_PROBES.get(cli_key)
    if not probes:
        return True  # unknown CLI: assume available (PATH check happens elsewhere)
    for selector, is_env in probes:
        if is_env:
            key = str(selector)[len("env:") :] if str(selector).startswith("env:") else str(selector)
            if _env_probe_present(key):
                return True
        else:
            if _file_probe_present(str(selector)):
                return True
    return False


def _resolve_key(value: Any) -> str | None:
    """Return the api_key string, honoring 'env:NAME' (recommended) or a literal."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower().startswith("env:"):
        key = text[4:].strip()
        return os.environ.get(key) or None
    return text or None


@dataclass
class ProviderConfig:
    """One execution-layer provider from the ``providers`` config block."""

    name: str
    provider_type: str  # "official_cli" | "openai_compat"
    cli: str | None = None              # for official_cli
    base_url: str | None = None         # for openai_compat
    api_key: str | None = None          # resolved (env expanded)
    models: list[str] = field(default_factory=list)
    api_format: str = "auto"  # "anthropic" | "openai" | "both" | "auto" (probe at first use)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def target_agent(self) -> str:
        """The route_agent_task target_agent alias this provider is registered under."""
        return self.name

    @property
    def is_official(self) -> bool:
        return self.provider_type == "official_cli"

    @property
    def model_ids(self) -> list[str]:
        """Decision-layer-choosable model list. Empty means 'any' for that provider."""
        return list(self.models)

    def supports_protocol(self, protocol: str) -> bool:
        """Whether this provider can be driven through ``protocol`` ('anthropic'|'openai')."""
        fmt = (self.api_format or "auto").strip().lower()
        if fmt == "both":
            return True
        if fmt in ("anthropic", "openai"):
            return fmt == protocol
        # auto: assume the protocol works; a failed execution reports upstream_error.
        return True


def _parse_providers_block(block: Any) -> list[ProviderConfig]:
    """Parse the ``providers`` config dict into ProviderConfig list (malformed skipped)."""
    if not isinstance(block, dict):
        return []
    out: list[ProviderConfig] = []
    for name, spec in block.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(spec, dict):
            continue
        provider_type = str(spec.get("type") or "").strip().lower()
        api_format = str(spec.get("api_format") or spec.get("protocol") or "auto").strip().lower()
        if api_format not in {"anthropic", "openai", "both", "auto"}:
            api_format = "auto"
        cli = str(spec.get("cli") or "").strip() or None
        if provider_type == "official_cli":
            out.append(
                ProviderConfig(
                    name=name,
                    provider_type="official_cli",
                    cli=cli,
                    models=[str(m) for m in (spec.get("models") or []) if str(m).strip()],
                    api_format=api_format,
                    raw=spec,
                )
            )
            continue
        # openai_compat (default when type missing/unknown pretends to be that)
        base_url = str(spec.get("base_url") or "").strip()
        out.append(
            ProviderConfig(
                name=name,
                provider_type="openai_compat",
                base_url=base_url or None,
                api_key=_resolve_key(spec.get("api_key")),
                models=[str(m) for m in (spec.get("models") or []) if str(m).strip()],
                api_format=api_format,
                raw=spec,
            )
        )
    return out


def providers_from_config(config: dict[str, Any]) -> list[ProviderConfig]:
    """Extract providers from a loaded config dict."""
    return _parse_providers_block(config.get("providers") or {})


# ---------------------------------------------------------------------------
# cc-switch importer: turn cc-switch's provider registry into our providers.
# ---------------------------------------------------------------------------
def import_from_ccswitch(
    db_path: str | Path | None = None,
    apps: tuple[str, ...] = ("claude", "codex"),
    env_indirect: bool = False,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Read providers from a cc-switch SQLite DB and return (providers_dict, key_env).

    ``providers_dict`` is ready to merge into config.json's ``providers`` block.
    ``key_env`` is a dict ``{ENV_VAR: secret}`` of keys that were extracted from the
    provider config — only populated when ``env_indirect=True``.

    Supported:
      * app_type='claude': settings_config.env with ANTHROPIC_BASE_URL +
        ANTHROPIC_AUTH_TOKEN (+ model mapping) -> openai_compat provider.
      * app_type='codex': settings_config.auth.OPENAI_API_KEY + config TOML with
        base_url/model -> openai_compat provider.
      * Official providers (empty env / OAuth tokens) are SKIPPED: they map to the
        built-in official_cli entry, not to a new relay provider.

    Never writes the DB; reads only. When ``env_indirect`` is True, API keys are NOT
    embedded: each provider gets ``"api_key": "env:CCSWITCH_<NAME>_API_KEY"`` and the
    actual keys are collected into ``key_env`` for the caller to persist OUTSIDE the
    repo (e.g. a git-ignored local file).
    """
    import json
    import re
    import sqlite3

    if db_path is None:
        db_path = Path.home() / ".cc-switch" / "cc-switch.db"
    db = Path(db_path)
    if not db.is_file():
        raise FileNotFoundError(f"cc-switch db not found: {db}")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT app_type, name, settings_config FROM providers"
        ).fetchall()
    finally:
        conn.close()

    out: dict[str, dict[str, Any]] = {}
    key_env: dict[str, str] = {}

    def _safe_env_name(name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").upper()
        return safe or "PROVIDER"

    for row in rows:
        app = str(row["app_type"] or "")
        if app not in apps:
            continue
        name = str(row["name"] or "").strip()
        if not name or name in out:
            continue
        try:
            sc = json.loads(row["settings_config"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(sc, dict):
            continue

        provider: dict[str, Any] | None = None
        if app == "claude":
            provider = _claude_settings_to_provider(name, sc)
        elif app == "codex":
            provider = _codex_settings_to_provider(name, sc)
        if not provider:
            continue
        if env_indirect and provider.get("api_key"):
            env_key = f"CCSWITCH_{_safe_env_name(name)}_API_KEY"
            key_env[env_key] = str(provider.pop("api_key"))
            provider["api_key"] = f"env:{env_key}"
        out[name] = provider
    return out, key_env


def _claude_settings_to_provider(name: str, sc: dict[str, Any]) -> dict[str, Any] | None:
    env = sc.get("env") if isinstance(sc.get("env"), dict) else {}
    base = str(env.get("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
    token = str(env.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    if not base or not token:
        return None  # official or incomplete
    # Collect model mapping (both Claude tiers and the generic model).
    models: list[str] = []
    for key in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_FABLE_MODEL",
    ):
        m = str(env.get(key) or "").strip()
        if m and m not in models:
            models.append(m)
    provider: dict[str, Any] = {
        "type": "openai_compat",
        "base_url": base,
        "api_key": token,
        "models": models,
        # cc-switch claude providers speak Anthropic Messages API (ANTHROPIC_BASE_URL).
        "api_format": "anthropic",
        "description": f"imported from cc-switch (claude)",
    }
    return provider


def _codex_settings_to_provider(name: str, sc: dict[str, Any]) -> dict[str, Any] | None:
    auth = sc.get("auth") if isinstance(sc.get("auth"), dict) else {}
    key = str(auth.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return None  # OAuth official (auth_mode=chatgpt, tokens) -> skip
    # Parse config TOML for base_url + model.
    config_text = str(sc.get("config") or "")
    base = None
    model = None
    if config_text:
        import re

        for line in config_text.splitlines():
            line = line.strip()
            m_url = re.match(r'^\s*base_url\s*=\s*"([^"]+)"', line)
            m_model = re.match(r"^\s*model\s*=\s*\"([^\"]+)\"", line)
            if m_url and not base:
                base = m_url.group(1).strip().rstrip("/")
            if m_model and not model:
                model = m_model.group(1).strip()
    if not base:
        return None
    provider: dict[str, Any] = {
        "type": "openai_compat",
        "base_url": base,
        "api_key": key,
        "models": [model] if model else [],
        # cc-switch codex providers speak OpenAI API (OPENAI_API_KEY + base_url).
        "api_format": "openai",
        "description": "imported from cc-switch (codex)",
    }
    return provider


def ccswitch_config_block(
    db_path: str | Path | None = None,
    apps: tuple[str, ...] = ("claude", "codex"),
    env_indirect: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (providers_block, key_env) from cc-switch, ready to merge into config.json.

    ``providers_block`` = {'providers': {...}}. ``key_env`` = {ENV_VAR: secret} when
    ``env_indirect=True`` (keys referenced as ``env:...`` in the block, secrets returned
    separately for safe local storage).
    """
    providers_dict, key_env = import_from_ccswitch(db_path, apps, env_indirect=env_indirect)
    return {"providers": providers_dict}, key_env


def write_ccswitch_env_file(key_env: dict[str, str], path: str | Path) -> Path:
    """Persist imported secrets to a LOCAL env file (e.g. ~/.agent-broker/ccswitch-keys.env).

    Format is ``KEY=value`` lines (no ``export`` prefix so it can be sourced by both
    PowerShell and bash-ish tooling); file is created with 0600 on POSIX. The caller is
    responsible for keeping this file OUT of the repository.
    """
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in sorted(key_env.items())]
    content = "\n".join(lines) + ("\n" if lines else "")
    target.write_text(content, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target
