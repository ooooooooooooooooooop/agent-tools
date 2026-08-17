"""claude_sdk_backend.py — opt-in Claude Agent SDK feasibility probe (EXPERIMENTAL).

Agent Switchboard's default Claude Code path is a dependency-free, raw stream-json
CLI round-trip (``claude -p --output-format stream-json ...``) that stays zero-dependency
(see ``consult_claude`` in agent_broker_mcp.py). The official `claude-agent-sdk` adds a
first-party Python driver with capabilities the raw CLI path does not expose uniformly:

  * first-class ``set_model`` / ``set_permission_mode`` at runtime on a LIVE client;
  * native ``interrupt`` / ``stop_task`` control-surface verbs;
  * ``fork_session`` / ``resume`` / ``continue_conversation`` and a pluggable
    ``session_store``;
  * streaming ``query`` message iterator and typed message/usage introspections.

This module does NOT replace the default backend. It is a deterministic capability
probe (mirroring `goal probe` / `smoke-managed-claude.py` honesty) that tells you
whether the SDK is installed and which command path it exposes, plus an OPT-IN
``--run-prompt`` driver that actually calls a model. By default it spends zero tokens.

It is intentionally a separate optional file: the broker keeps zero-dependency by
default; importing this module is lazy and only triggered when a caller asks for the
SDK backend.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# A vendored SDK install (e.g. `pip install --target ./_sdk_probe_deps claude-agent-sdk`)
# is picked up first so the broker source stays SDK-free; then any site import.
_SDK_DEPS_CANDIDATES = (
    Path(__file__).resolve().parent / "_sdk_probe_deps",
    Path(__file__).resolve().parent / "sdk_deps",
)

CLAUDE_EXTENSIONS = ("claude", "claude.cmd", "claude.ps1")


def _claude_cli_path() -> str | None:
    for name in CLAUDE_EXTENSIONS:
        found = shutil.which(name)
        if found:
            return found
    return None


def sdk_importable() -> tuple[bool, str | None]:
    """Whether the claude-agent-sdk is importable, and from where. Read-only, no SDK import
    side effects beyond a find_spec probe."""
    if importlib.util.find_spec("claude_agent_sdk") is not None:
        return True, "installed-site-packages"
    for candidate in _SDK_DEPS_CANDIDATES:
        if candidate.is_dir() and (candidate / "claude_agent_sdk").is_dir():
            return True, str(candidate)
    return False, None


def probe_sdk_capabilities(claude_path: str | None = None) -> dict[str, Any]:
    """Deterministic, local, read-only report of the SDK surface available on this machine.

    Honesty contract: reports ``available`` only when the SDK actually imports; reports
    the SDK's public control-surface names it can exercise; reports the Claude CLI it
    would drive; and a ``top_level`` list of how the two paths compare. Never calls a
    model and never mutates anything."""
    cli = claude_path or _claude_cli_path()
    sdk_ok, sdk_source = sdk_importable()

    result: dict[str, Any] = {
        "available": sdk_ok,
        "sdk_source": sdk_source,
        "sdk_driver": "claude_agent_sdk.query / ClaudeSDKClient",
        "claude_cli": cli,
        "claude_cli_found": bool(cli),
        "opt_in_required": True,
        "note": None,
    }
    if not sdk_ok:
        result["note"] = (
            "claude-agent-sdk not importable. Install it (pip install claude-agent-sdk) or "
            "vend locally (pip install --target <dir> claude-agent-sdk) and point "
            "AGENT_BROKER_CLAUDE_SDK_DEPS at it; the broker keeps its zero-dependency "
            "CLI path as the default regardless."
        )
        return result

    exposed = {
        "query_stream": "query(...) -> AsyncIterator[message|stream-event]",
        "live_client": "ClaudeSDKClient.set_model/ set_permission_mode/ interrupt/ stop_task",
        "session_ops": "fork_session / resume / continue_conversation / session_store",
        "typed_introspection": "ResultMessage.usage / ModelUsage / SdkBeta types",
    }
    result["sdk_exposes"] = exposed
    result["vs_cli"] = {
        "model_switch_runtime": True,  # set_model on live client; CLI must respawn with --model
        "native_interrupt": True,      # interrupt() control-surface verb; CLI needs taskkill/stream control
        "resume_via_session_id": True, # CLI already supports --resume; SDK adds typed store
        "zero_dependency": False,      # SDK is an extra dependency the default CLI path avoids
    }
    return result


def _sdk_report_for_doctor() -> dict[str, Any]:
    try:
        rep = probe_sdk_capabilities()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return rep


# --- OPT-IN real-model driver (only runs with --run-prompt) --------------------

async def run_sdk_driver(prompt: str, *, model: str | None = None, max_turns: int = 5,
                         cwd: str | None = None, claude_path: str | None = None,
                         debug_stderr: bool = False) -> dict[str, Any]:
    """EXPERIMENTAL, OPT-IN: drive one SDK-backed model call with ``query`` and collect
    the result. This SPENDS model tokens; it is a feasibility probe only, never part of the
    automatic route. Missing SDK or CLI fails closed."""
    sdk_ok, sdk_source = sdk_importable()
    if not sdk_ok:
        return {"ran": False, "error": "claude_agent_sdk not importable"}
    if not sdk_source or sdk_source == "installed-site-packages":
        pass  # importable from the environment
    else:
        if sdk_source not in sys.path:
            sys.path.insert(0, sdk_source)

    try:
        import claude_agent_sdk
        from claude_agent_sdk import ClaudeAgentOptions
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "error": f"sdk_import_failed: {exc}"}

    cli = claude_path or _claude_cli_path()
    if not cli:
        return {"ran": False, "error": "claude CLI not found; cannot drive the SDK"}

    kwargs: dict[str, Any] = {}
    if model:
        kwargs["model"] = model
    kwargs["permission_mode"] = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") or "plan"  # default safe
    kwargs["max_turns"] = int(max_turns)
    kwargs["cwd"] = cwd or os.fspath(Path.cwd())
    kwargs["cli_path"] = cli
    kwargs["stderr"] = subprocess.DEVNULL if not debug_stderr else None

    raw: list[str] = []
    usage: dict[str, Any] | None = None
    try:
        async for message in claude_agent_sdk.query(
            prompt=prompt,
            options=ClaudeAgentOptions(**kwargs),
        ):
            if hasattr(message, "type"):
                mtype = getattr(message, "type", None)
                if mtype == "assistant":
                    raw.append(getattr(message, "text", "") or "")
                elif mtype == "result":
                    usage = getattr(message, "usage", None)
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "error": f"sdk_driver_failed: {type(exc).__name__}: {exc}"}

    usage_dict: dict[str, Any] | None = None
    if usage is not None and hasattr(usage, "__dict__"):
        try:
            usage_dict = {k: v for k, v in vars(usage).items()}
        except TypeError:
            usage_dict = {"raw": str(usage)}

    return {
        "ran": True,
        "driver": "claude_agent_sdk.query",
        "prompt_len_chars": len(prompt),
        "assistant_text": "\n".join(raw).strip(),
        "assistant_text_len": len("\n".join(raw).strip()),
        "usage": usage_dict,
        "model": model,
        "note": "EXPERIMENTAL SDK probe - not the default broker route.",
    }


# --- CLI dispatch for the probe -------------------------------------------------

SDK_HELP = (
    "Usage: agent_broker_mcp.py probe sdk [--json] [--run-prompt <text>] [--model <name>] [--max-turns <n>] [--debug]"
)


def handle_sdk_probe_cli(argv: list[str]) -> int:
    """optionally call a model ONLY when --run-prompt is given; otherwise a free capability probe."""
    import asyncio

    if not argv or argv[0] in {"help", "-h", "--help"}:
        print(SDK_HELP)
        return 0
    json_flag = "--json" in argv

    def _arg(name: str) -> str | None:
        if name not in argv:
            return None
        index = argv.index(name)
        return argv[index + 1] if index + 1 < len(argv) else None

    prompt = _arg("--run-prompt")
    if prompt is None:
        rep = probe_sdk_capabilities()
        out = {"probe": "capability", **rep}
        print(json.dumps(out, ensure_ascii=True, indent=2))
        return 0

    model = _arg("--model") if "--model" in argv else None
    max_turns = 5
    if "--max-turns" in argv:
        try:
            max_turns = int(_arg("--max-turns"))
        except (TypeError, ValueError):
            pass
    try:
        result = asyncio.run(
            run_sdk_driver(
                prompt,
                model=model,
                max_turns=max_turns,
                debug_stderr="--debug" in argv,
            )
        )
    except Exception as exc:  # noqa: BLE001
        result = {"ran": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result.get("ran") else 1
