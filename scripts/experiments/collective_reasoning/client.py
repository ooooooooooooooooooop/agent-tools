#!/usr/bin/env python3
"""Model client for the collective-reasoning experiment (isolated research branch).

Reuses the device's existing model-access surfaces; creates no new registry,
credential system, or fallback layer:

- ``cpa``  -> local CLIProxyAPI gateway declared in ``registry/providers.yaml``
              (key loaded at runtime from the gateway's own config file).
- ``bai``  -> direct provider declared in ``registry/providers.yaml``
              (key loaded at runtime from ``~/.dsh/.credentials.yaml`` refs).
- ``kimi`` -> direct provider declared in ``registry/providers.yaml``
              (key loaded at runtime from ``~/.dsh/.credentials.yaml`` refs).

Every call is recorded with full provenance (requested model, reported model,
token usage, latency) and cached idempotently under the run's ``calls/`` dir.
Key material is never written to artifacts: only request messages and response
bodies are stored, and prompts never contain credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
CPA_CONFIG = Path(
    r"C:\Users\yexue\Downloads\EasyCLIProxyAPI-v0.2.25-Windows-amd64"
    r"\EasyCLIProxyAPI-v0.2.25-Windows-amd64\cpa-core\config.yaml"
)
CREDENTIALS_FILE = Path.home() / ".dsh" / ".credentials.yaml"

PROVIDERS: dict[str, dict[str, str]] = {
    "cpa": {"base": "http://127.0.0.1:8317/v1", "auth": "cpa-config"},
    "bai": {"base": "https://api.b.ai/v1", "auth": "credentials-refs"},
    "kimi": {"base": "https://api.kimi.com/coding/v1", "auth": "credentials-refs"},
}

# Model roster. Participant pool = 5 distinct model families; judges and
# utility models sit outside the pool so they never appear as participants.
MODELS: dict[str, dict[str, str]] = {
    # participant pool (INDEPENDENT / COUNCIL / COLLECTIVE)
    "claude-sonnet": {"provider": "cpa", "id": "claude-sonnet-4-6", "family": "anthropic"},
    "gemini-3.8": {"provider": "cpa", "id": "gemini-3.8-flash-high", "family": "google"},
    "glm-5.3": {"provider": "bai", "id": "glm-5.3-flash", "family": "zhipu"},
    "qwen-3.8": {"provider": "bai", "id": "qwen3.8-flash", "family": "alibaba"},
    "kimi-k3": {"provider": "kimi", "id": "k3-256k", "family": "moonshot"},
    # judges (never participate in any condition)
    "judge-claude-opus": {"provider": "cpa", "id": "claude-opus-4-6-thinking", "family": "anthropic"},
    "judge-gemini-pro": {"provider": "cpa", "id": "gemini-3.1-pro-low", "family": "google"},
    # utility roles (outside pool: stopping evaluator / blind-spot search / renderer)
    "util-gemini-3.7": {"provider": "cpa", "id": "gemini-3.7-flash-high", "family": "google"},
}

PARTICIPANTS = ["claude-sonnet", "gemini-3.8", "glm-5.3", "qwen-3.8", "kimi-k3"]
JUDGES = ["judge-claude-opus", "judge-gemini-pro"]

_key_cache: dict[str, str] = {}
_key_lock = threading.Lock()


def _load_yaml(path: Path) -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def provider_key(provider: str) -> str:
    """Resolve the API key for a provider at runtime; never log or persist it."""
    with _key_lock:
        if provider in _key_cache:
            return _key_cache[provider]
        if provider == "cpa":
            cfg = _load_yaml(CPA_CONFIG)
            key = str(cfg["api-keys"][0])
        elif provider in ("bai", "kimi"):
            creds = _load_yaml(CREDENTIALS_FILE)
            env_name = {"bai": "BAI_API_KEY", "kimi": "KIMI_CODING_API_KEY"}[provider]
            key = str(creds["refs"][env_name])
        else:
            raise KeyError(provider)
        _key_cache[provider] = key
        return key


def call_key(run_id: str, tag: str, model_alias: str, messages: list, max_tokens: int) -> str:
    payload = json.dumps(
        {"run": run_id, "tag": tag, "model": model_alias, "messages": messages, "max_tokens": max_tokens},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ModelCallError(RuntimeError):
    pass


def call_model(
    model_alias: str,
    messages: list,
    *,
    run_id: str,
    tag: str,
    calls_dir: Path,
    max_tokens: int = 8000,
    timeout_s: int = 240,
    retries: int = 3,
    force: bool = False,
) -> dict:
    """Call a model once (idempotent via cache) and return a provenance record."""
    spec = MODELS[model_alias]
    provider = spec["provider"]
    ckey = call_key(run_id, tag, model_alias, messages, max_tokens)
    cache_path = calls_dir / f"{ckey}.json"
    if cache_path.exists() and not force:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    body = json.dumps(
        {"model": spec["id"], "messages": messages, "max_tokens": max_tokens}
    ).encode("utf-8")
    url = PROVIDERS[provider]["base"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider_key(provider)}",
        "Content-Type": "application/json",
    }

    last_err: Any = None
    for attempt in range(retries):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.load(resp)
            latency = round(time.time() - t0, 2)
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            record = {
                "key": ckey,
                "run_id": run_id,
                "tag": tag,
                "model_alias": model_alias,
                "provider": provider,
                "requested_model": spec["id"],
                "reported_model": data.get("model"),
                "family": spec.get("family"),
                "usage": data.get("usage"),
                "latency_s": latency,
                "attempt": attempt + 1,
                "content": message.get("content"),
                "reasoning_content": message.get("reasoning_content"),
                "error": None,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            _atomic_write(cache_path, record)
            return record
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            last_err = f"HTTP {exc.code}: {detail}"
            if exc.code in (401, 403, 404):
                break  # non-retryable
        except Exception as exc:  # noqa: BLE001 - record everything
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(min(60, 5 * (2 ** attempt)))

    record = {
        "key": ckey, "run_id": run_id, "tag": tag, "model_alias": model_alias,
        "provider": provider, "requested_model": spec["id"], "reported_model": None,
        "family": spec.get("family"), "usage": None, "latency_s": None,
        "attempt": retries, "content": None, "reasoning_content": None,
        "error": last_err, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _atomic_write(cache_path, record)
    return record


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def map_pool(fn, items, max_workers: int = 5):
    """Map fn over items with bounded concurrency, preserving order."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(fn, items))
