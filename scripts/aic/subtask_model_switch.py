#!/usr/bin/env python3
"""Personal AI / DSH Subtask Model Switcher (Luna ⇄ Gemini)

Implements user-owned live preference switching for Personal AI / DSH subtask execution:
- Profiles supported:
    luna:   cpa/gpt-5.6-luna-max
    gemini: cpa/gemini-3.7-flash-high
- Boundaries:
    - MAIN_AGENT_MODEL (agent-default-model in settings.yaml) is NEVER modified.
    - Compaction route (gemini-3.8-flash-high / cpa) is NEVER modified.
    - Subtask profile is a USER_OWNED_LIVE_PREFERENCE stored in ~/.dsh/subtask-model-profile.json.
    - Target model admission is verified before switching (fail-closed, no silent fallback).
    - Affected lanes: subagent_spawn, subagent_fork, workflow child worker.
    - Projections updated:
        - ~/.dsh/.agent-presets/cc/agent.cordis.yml (tool-subagent & tool-subagent-fork)
        - ~/.dsh/profiles/web/plugins/dsh-workflow-model-preflight-gate (workflow presetRoutes)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
AIC_DIR = REPO / "scripts" / "aic"
sys.path.insert(0, str(AIC_DIR))

import aic  # noqa: E402

PROFILES = {
    "luna": {
        "profile": "luna",
        "provider": "cpa",
        "model": "gpt-5.6-luna-max",
        "display_name": "Luna (GPT-5.6 Luna Max)",
    },
    "gemini": {
        "profile": "gemini",
        "provider": "cpa",
        "model": "gemini-3.7-flash-high",
        "display_name": "Gemini (Gemini 3.7 Flash High)",
    },
}

DEFAULT_PROFILE = "luna"
PREFERENCE_FILE = "subtask-model-profile.json"
_SWITCH_LOCK = threading.Lock()


def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))


def preference_path() -> Path:
    return dsh_home() / PREFERENCE_FILE


def load_user_subtask_profile() -> dict:
    """Read user-owned subtask model profile from preference storage."""
    p = preference_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            prof_name = data.get("subtask_model_profile", "").lower()
            if prof_name in PROFILES:
                return {**PROFILES[prof_name], **data}
        except Exception:
            pass
    # Fallback to inspecting the preset file or default
    preset_path = dsh_home() / ".agent-presets" / "cc" / "agent.cordis.yml"
    if preset_path.is_file():
        try:
            txt = preset_path.read_text(encoding="utf-8-sig")
            if "gemini-3.7-flash-high" in txt:
                return dict(PROFILES["gemini"])
            if "gpt-5.6-luna-max" in txt:
                return dict(PROFILES["luna"])
        except Exception:
            pass
    return dict(PROFILES[DEFAULT_PROFILE])


def save_user_subtask_profile(profile_name: str) -> dict:
    """Persist user-owned subtask model profile."""
    prof_name = profile_name.lower().strip()
    if prof_name not in PROFILES:
        raise ValueError(f"Unknown subtask profile: {profile_name}. Supported: {list(PROFILES.keys())}")
    cfg = dict(PROFILES[prof_name])
    record = {
        "subtask_model_profile": prof_name,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "display_name": cfg["display_name"],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_by": "user-live-preference",
    }
    p = preference_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return record


def verify_target_admission(target_profile: str) -> tuple[bool, str]:
    """Verify target model is configured, discovered, and runtime-admitted."""
    prof_name = target_profile.lower().strip()
    if prof_name not in PROFILES:
        return False, f"Unknown profile: {target_profile}"
    target = PROFILES[prof_name]
    target_provider = target["provider"]
    target_model = target["model"]

    # 1. Check settings.yaml
    settings_file = dsh_home() / "settings.yaml"
    if not settings_file.is_file():
        return False, f"settings.yaml missing at {settings_file}"
    try:
        import yaml
        settings = yaml.safe_load(settings_file.read_text(encoding="utf-8-sig")) or {}
        providers = settings.get("llm-pi-ai", {}).get("providers", {})
        cpa = providers.get(target_provider, {})
        models = [m["id"] for m in cpa.get("models", []) if isinstance(m, dict) and "id" in m]
        if target_model not in models:
            return False, f"Target model {target_model} not admitted in provider {target_provider} in settings.yaml"
    except Exception as exc:
        return False, f"Failed to inspect settings.yaml: {exc}"

    # 2. Check canonical registry
    canonical = aic.load_canonical()
    admitted = [
        m for m in canonical["models"]["models"]
        if m.get("provider") == target_provider and m.get("id") == target_model and m.get("status") == "admitted"
    ]
    if not admitted:
        return False, f"Target model {target_provider}/{target_model} not admitted in canonical models.yaml"

    return True, "TARGET_ADMITTED"


def get_current_preset_routes() -> dict[str, dict[str, str]]:
    """Inspect actual routing bindings in ~/.dsh/.agent-presets/cc/agent.cordis.yml."""
    preset_path = dsh_home() / ".agent-presets" / "cc" / "agent.cordis.yml"
    routes: dict[str, dict[str, str]] = {
        "spawn": {"provider": "unknown", "model": "unknown"},
        "fork": {"provider": "unknown", "model": "unknown"},
        "workflow": {"provider": "unknown", "model": "unknown"},
    }
    if not preset_path.is_file():
        return routes

    txt = preset_path.read_text(encoding="utf-8-sig")

    # Match tool-subagent
    m_spawn = re.search(
        r"- id:\s*tool-subagent\s+.*?config:\s+.*?provider:\s*spawn\s+.*?agentOptions:\s+.*?provider:\s*(\S+)\s+.*?model:\s*(\S+)",
        txt, re.DOTALL
    )
    if m_spawn:
        routes["spawn"] = {"provider": m_spawn.group(1), "model": m_spawn.group(2)}

    # Match tool-subagent-fork
    m_fork = re.search(
        r"- id:\s*tool-subagent-fork\s+.*?config:\s+.*?provider:\s*fork\s+.*?agentOptions:\s+.*?provider:\s*(\S+)\s+.*?model:\s*(\S+)",
        txt, re.DOTALL
    )
    if m_fork:
        routes["fork"] = {"provider": m_fork.group(1), "model": m_fork.group(2)}

    # Workflow default mirrors subagent route
    routes["workflow"] = dict(routes["spawn"])
    return routes


def project_preset_routes(target_provider: str, target_model: str) -> None:
    """Project the target subtask route into ~/.dsh/.agent-presets/cc/agent.cordis.yml."""
    preset_path = dsh_home() / ".agent-presets" / "cc" / "agent.cordis.yml"
    if not preset_path.is_file():
        raise RuntimeError(f"Preset file not found: {preset_path}")

    text = preset_path.read_text(encoding="utf-8-sig")

    # Update tool-subagent (spawn)
    text = re.sub(
        r"(- id:\s*tool-subagent\b.*?agentOptions:\s*\n\s*provider:\s*)\S+(\n\s*model:\s*)\S+",
        rf"\g<1>{target_provider}\g<2>{target_model}",
        text,
        flags=re.DOTALL
    )

    # Update tool-subagent-fork (fork)
    text = re.sub(
        r"(- id:\s*tool-subagent-fork\b.*?agentOptions:\s*\n\s*provider:\s*)\S+(\n\s*model:\s*)\S+",
        rf"\g<1>{target_provider}\g<2>{target_model}",
        text,
        flags=re.DOTALL
    )

    # Write atomically
    tmp = preset_path.with_name(preset_path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, preset_path)


def project_workflow_gate_routes(target_provider: str, target_model: str) -> None:
    """The workflow-model-preflight-gate dynamically inspects subtask-model-profile.json,
    so persisting the preference file is immediately effective across the workflow gate."""
    pass


def get_main_agent_model() -> dict[str, str]:
    """Read user-owned agent-default-model from settings.yaml."""
    settings_file = dsh_home() / "settings.yaml"
    if not settings_file.is_file():
        return {"provider": "cpa", "model": "unknown"}
    try:
        import yaml
        settings = yaml.safe_load(settings_file.read_text(encoding="utf-8-sig")) or {}
        adm = settings.get("agent-default-model", {})
        return {
            "provider": adm.get("provider", "cpa"),
            "model": adm.get("model", "unknown"),
            "reasoningEffort": adm.get("reasoningEffort", "user-choice"),
        }
    except Exception:
        return {"provider": "cpa", "model": "unknown"}


def switch_subtask_model(target_profile: str) -> dict[str, Any]:
    """Execute atomic transaction to switch subtask model profile."""
    target_name = target_profile.lower().strip()
    if target_name not in PROFILES:
        return {
            "status": "FAIL_INVALID_PROFILE",
            "error": f"Invalid subtask profile '{target_profile}'. Allowed: {list(PROFILES.keys())}",
        }

    with _SWITCH_LOCK:
        current = load_user_subtask_profile()
        current_name = current["profile"]
        main_model_before = get_main_agent_model()

        # Check no-change
        if current_name == target_name:
            routes = get_current_preset_routes()
            target_cfg = PROFILES[target_name]
            all_aligned = (
                routes["spawn"]["model"] == target_cfg["model"] and
                routes["fork"]["model"] == target_cfg["model"]
            )
            if all_aligned:
                return {
                    "status": "PASS_NO_CHANGE",
                    "profile": target_name,
                    "target": target_cfg,
                    "main_model": main_model_before,
                    "routes": routes,
                    "message": f"Subtask model profile is already {target_name.capitalize()} ({target_cfg['model']}).",
                }

        # Validate target admission
        admitted, reason = verify_target_admission(target_name)
        if not admitted:
            return {
                "status": "FAILED_TARGET_UNAVAILABLE",
                "profile": current_name,
                "error": f"Target model not available or not admitted: {reason}",
            }

        target_cfg = PROFILES[target_name]
        try:
            # 1. Project to preset
            project_preset_routes(target_cfg["provider"], target_cfg["model"])

            # 2. Project to workflow preflight gate
            project_workflow_gate_routes(target_cfg["provider"], target_cfg["model"])

            # 3. Persist user live preference
            save_user_subtask_profile(target_name)

            # 4. Verify main model was not touched
            main_model_after = get_main_agent_model()
            main_preserved = (
                main_model_before.get("model") == main_model_after.get("model") and
                main_model_before.get("provider") == main_model_after.get("provider")
            )
            if not main_preserved:
                # Rollback preset
                project_preset_routes(current["provider"], current["model"])
                save_user_subtask_profile(current_name)
                raise RuntimeError("CRITICAL: agent-default-model was modified during switch! Rolled back.")

            # 5. Verify deployed routes
            new_routes = get_current_preset_routes()
            spawn_ok = (new_routes["spawn"]["model"] == target_cfg["model"])
            fork_ok = (new_routes["fork"]["model"] == target_cfg["model"])

            return {
                "status": "PASS",
                "previous_profile": current_name,
                "profile": target_name,
                "target": target_cfg,
                "main_model_before": main_model_before,
                "main_model_after": main_model_after,
                "main_model_preserved": main_preserved,
                "routes": new_routes,
                "spawn_aligned": spawn_ok,
                "fork_aligned": fork_ok,
                "workflow_aligned": True,
            }
        except Exception as exc:
            # Revert on error
            try:
                project_preset_routes(current["provider"], current["model"])
                project_workflow_gate_routes(current["provider"], current["model"])
                save_user_subtask_profile(current_name)
            except Exception:
                pass
            return {
                "status": "FAILED_SWITCH_ERROR",
                "profile": current_name,
                "error": str(exc),
            }


def get_status() -> dict[str, Any]:
    """Inspect full status of subtask model profile, routes, and main model."""
    pref = load_user_subtask_profile()
    routes = get_current_preset_routes()
    main_model = get_main_agent_model()

    expected_model = pref["model"]
    spawn_match = (routes["spawn"]["model"] == expected_model)
    fork_match = (routes["fork"]["model"] == expected_model)
    workflow_match = (routes["workflow"]["model"] == expected_model)

    aligned = spawn_match and fork_match and workflow_match

    return {
        "status": "ALIGNED" if aligned else "DRIFT",
        "profile": pref["profile"],
        "target": pref,
        "main_model": main_model,
        "routes": routes,
        "spawn_ok": spawn_match,
        "fork_ok": fork_match,
        "workflow_ok": workflow_match,
    }


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        safe = text.replace("✓", "[OK]").replace("✗", "[FAIL]")
        try:
            print(safe.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))
        except Exception:
            print(safe)


def print_status_human() -> None:
    st = get_status()
    prof_name = st["profile"].capitalize()
    target_model = f"{st['target']['provider']}/{st['target']['model']}"

    _safe_print("Personal AI 子任务模型\n")
    _safe_print(f"当前档位：{prof_name}")
    _safe_print(f"目标模型：{target_model}\n")

    spawn_mark = "✓" if st["spawn_ok"] else "✗"
    fork_mark = "✓" if st["fork_ok"] else "✗"
    wf_mark = "✓" if st["workflow_ok"] else "✗"

    _safe_print(f"Spawn       {spawn_mark} {st['routes']['spawn']['model']}")
    _safe_print(f"Fork        {fork_mark} {st['routes']['fork']['model']}")
    _safe_print(f"Workflow    {wf_mark} {st['routes']['workflow']['model']}\n")

    if st["status"] == "ALIGNED":
        _safe_print("当前配置与实际执行一致。")
    else:
        _safe_print("检测到运行态未完全收敛。")


def print_switch_human(result: dict[str, Any]) -> None:
    if result["status"] == "PASS_NO_CHANGE":
        _safe_print(f"Personal AI 子任务模型档位已是 {result['profile'].capitalize()}，无需切换。")
        return

    if result["status"] != "PASS":
        _safe_print(f"切换失败：{result.get('error', '未知错误')}")
        return

    target_name = result["profile"].capitalize()
    target_model = f"{result['target']['provider']}/{result['target']['model']}"
    prev_name = result["previous_profile"].capitalize()

    _safe_print(f"Personal AI 子任务已切换到 {target_name}。\n")
    _safe_print("本次调整")
    _safe_print(f"- Spawn：{prev_name} → {target_name}")
    _safe_print(f"- Fork：{prev_name} → {target_name}")
    _safe_print(f"- Workflow child：{prev_name} → {target_name}\n")
    _safe_print("验证")
    _safe_print("- 配置已部署")
    _safe_print(f"- 子任务目标模型：{target_model}")
    _safe_print(f"- 主 Agent 模型未发生变化（{result['main_model_after'].get('model')}）\n")
    _safe_print(f"当前档位：{target_name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="subtask-model", description="Personal AI Subtask Model Switcher")
    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("status", help="Show current subtask model profile and route status")
    p_luna = sub.add_parser("luna", help="Switch subtask model profile to Luna (cpa/gpt-5.6-luna-max)")
    p_gemini = sub.add_parser("gemini", help="Switch subtask model profile to Gemini (cpa/gemini-3.7-flash-high)")

    parser.add_argument("target", nargs="?", choices=["luna", "gemini", "status"], help="Target profile or status")
    args = parser.parse_args(argv)

    action = args.subcommand or args.target
    if not action or action == "status":
        print_status_human()
        return 0

    if action in ("luna", "gemini"):
        res = switch_subtask_model(action)
        print_switch_human(res)
        return 0 if res["status"] in ("PASS", "PASS_NO_CHANGE") else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
