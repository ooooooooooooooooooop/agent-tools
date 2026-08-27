#!/usr/bin/env python3
"""aic — Thin AI Control Plane CLI (Architecture V2.1, Migration #2).

Boundary (frozen): discover / render / diff / validate / apply / bootstrap.
Forbidden: daemon, database, agent orchestration, LLM calls, memory queries,
runtime routing decisions, scheduling. This tool is offline and on-demand.

Phase-2 scope: DSH target only. TARGET BEHAVIOR = CURRENT BEHAVIOR.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]          # repo root (scripts/aic/aic.py)
REG = ROOT / "registry"
INV = REG / "inventory"                              # generated runtime inventory

DEVICE_PATH_RE = re.compile(r"[A-Za-z]:\\(?:Users|Windows|Program Files|Download)\\")


# ---------------------------------------------------------------- canonical IO

def load_yaml(path: Path):
    with path.open("r", encoding="utf-8-sig") as fh:
        return yaml.safe_load(fh)


_JS_TAG_RE = re.compile(r"!!js[^\n]*")


def load_cordis_yaml(path: Path):
    """Parse cordis composition YAML containing !!js expressions.

    Phase-2 field projection never diffs !!js-valued fields, so expressions are
    replaced with an opaque marker string before parsing.
    """
    text = path.read_text(encoding="utf-8-sig")
    return yaml.safe_load(_JS_TAG_RE.sub('"<js-expr>"', text))


def load_canonical() -> dict:
    return {
        "providers": load_yaml(REG / "providers.yaml"),
        "models": load_yaml(REG / "models.yaml"),
        "policy": load_yaml(REG / "routing-policy.yaml"),
        "gateways": load_yaml(REG / "gateways.yaml"),
        "runtime": load_yaml(REG / "runtime.lock.yaml"),
    }


def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))


def resolve_value_from(policy: dict, dotted: str):
    node = policy
    for part in dotted.split("."):
        node = node[part]
    return node


# ---------------------------------------------------------------- render (dsh)

def render_settings(canonical: dict, overlay: dict) -> dict:
    md = canonical["policy"]["rules"]["main_default"]
    out = {}
    out.update(overlay["overlay"]["settings"])  # ui-onboarding, agent-presets
    out["llm-pi-ai"] = {"providers": canonical["providers"]["providers"]}
    out["agent-default-model"] = {
        k: md[k] for k in ("provider", "model", "reasoningEffort") if k in md
    }
    return out


def adapter_contract() -> dict:
    return load_yaml(REG / "harnesses" / "dsh.yaml")


def adapter_overlay() -> dict:
    return load_yaml(REG / "harnesses" / "dsh-overlay.yaml")


def find_row(rows: list, row_id: str):
    """Find a cordis row by id, recursing into cordis:group config lists."""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("id") == row_id:
            return row
        inner = row.get("config")
        if isinstance(inner, list):
            found = find_row(inner, row_id)
            if found is not None:
                return found
    return None


def get_nested(obj, dotted: str):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return _MISSING
        obj = obj[part]
    return obj


class _Missing:
    def __repr__(self):
        return "<missing>"


_MISSING = _Missing()


# ---------------------------------------------------------------- diff helpers

def deep_diff(expected, actual, path: str, out: list):
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in expected:
                out.append((sub, "<absent>", actual[key]))
            elif key not in actual:
                out.append((sub, expected[key], "<absent>"))
            else:
                deep_diff(expected[key], actual[key], sub, out)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            out.append((f"{path}.length", len(expected), len(actual)))
        for i, (e, a) in enumerate(zip(expected, actual)):
            deep_diff(e, a, f"{path}[{i}]", out)
    elif expected != actual:
        out.append((path, expected, actual))


def cmd_render(args) -> int:
    canonical = load_canonical()
    overlay = adapter_overlay()
    expected = render_settings(canonical, overlay)
    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        with (outdir / "settings.yaml").open("w", encoding="utf-8") as fh:
            yaml.safe_dump(expected, fh, allow_unicode=True, sort_keys=False)
        print(f"rendered -> {outdir / 'settings.yaml'}")
    else:
        print(yaml.safe_dump(expected, allow_unicode=True, sort_keys=False))
    return 0


def cmd_diff(args) -> int:
    canonical = load_canonical()
    overlay = adapter_overlay()
    contract = adapter_contract()
    findings: list[tuple[str, str, object, object]] = []

    for target in contract["render_targets"]:
        actual_path = Path(target["path"].replace("{{DSH_HOME}}", str(dsh_home())))
        if not actual_path.is_file():
            findings.append((target["path"], "<file>", "present", "missing"))
            continue
        actual = (load_cordis_yaml(actual_path) if target["id"] == "agent-preset-cc"
                  else load_yaml(actual_path))
        if target["mode"] == "full-file":
            expected = render_settings(canonical, overlay)
            raw: list = []
            deep_diff(expected, actual, "", raw)
            for field, exp, act in raw:
                findings.append((target["path"], field, exp, act))
        elif target["mode"] == "field-projection":
            for check in target.get("field_checks", []):
                row = find_row(actual, check["row"])
                act = get_nested(row, check["key"]) if row is not None else _MISSING
                exp = resolve_value_from(canonical["policy"], check["value_from"])
                if act != exp:
                    findings.append((target["path"], f"{check['row']}.{check['key']}", exp, act))

    if not findings:
        print("NO DRIFT")
        return 0
    print("DRIFT detected:")
    for file, field, exp, act in findings:
        print(f"  file={file}\n    field={field}\n    expected={exp!r}\n    actual={act!r}")
    return 1


# ---------------------------------------------------------------- validate

def cmd_validate(_args) -> int:
    errors: list[str] = []
    canonical = load_canonical()
    providers = canonical["providers"]["providers"]
    models = canonical["models"]["models"]
    rules = canonical["policy"]["rules"]

    admitted = {(m["provider"], m["id"]) for m in models}
    for m in models:
        if m.get("status") != "admitted":
            errors.append(f"models.yaml: {m['id']} status={m.get('status')} (only 'admitted' allowed here)")
        if m["provider"] not in providers:
            errors.append(f"models.yaml: {m['id']} references unknown provider {m['provider']}")
    seen = set()
    for m in models:
        key = (m["provider"], m["id"])
        if key in seen:
            errors.append(f"models.yaml: duplicate {key}")
        seen.add(key)

    for pname, pdef in providers.items():
        for m in pdef.get("models", []) or []:
            if (pname, m["id"]) not in admitted:
                errors.append(f"providers.yaml: {pname}/{m['id']} declared but not admitted in models.yaml")

    for rname in ("main_default", "subagent_spawn", "subagent_fork", "compaction_summary"):
        rule = rules.get(rname, {})
        if (rule.get("provider"), rule.get("model")) not in admitted:
            errors.append(f"routing-policy: rule {rname} -> {rule.get('provider')}/{rule.get('model')} not admitted")

    for path in sorted(REG.rglob("*.yaml")):
        if INV in path.parents:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if DEVICE_PATH_RE.search(text):
            errors.append(f"{path.relative_to(REG)}: contains device absolute path (belongs in generated inventory)")

    try:
        contract = adapter_contract()
        for target in contract["render_targets"]:
            for check in target.get("field_checks", []):
                resolve_value_from(canonical["policy"], check["value_from"])
    except Exception as exc:  # noqa: BLE001 - report as validation error
        errors.append(f"harnesses/dsh.yaml: unresolvable value_from: {exc}")

    if errors:
        print("INVALID:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("VALID")
    return 0


# ---------------------------------------------------------------- discover

def _run(cmd: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex((host, port)) == 0


def _discover_cpa_models() -> dict:
    token = "123456"  # local dummy key (documented); prefer broker config when readable
    broker_cfg = Path.home() / ".agent-broker" / "config.json"
    try:
        token = json.loads(broker_cfg.read_text(encoding="utf-8"))["cli_backends"]["cpa"]["api_key"]
    except Exception:  # noqa: BLE001
        pass
    req = urllib.request.Request("http://127.0.0.1:8317/v1/models",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"reachable": True, "models": sorted(m["id"] for m in data.get("data", []))}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": str(exc), "models": []}


def cmd_discover(_args) -> int:
    inv: dict = {"generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
                 "generated_by": "aic discover", "class": "generated runtime inventory (NOT canonical)"}
    inv["device_id"] = platform.node() or getpass.getuser()
    inv["os"] = {"system": platform.system(), "release": platform.release(),
                 "version": platform.version(), "arch": platform.machine()}
    inv["cpu"] = {"cores": os.cpu_count()}
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                inv["cpu"]["name"] = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
        try:
            import ctypes

            class _MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                            ("ullAvailExtendedVirtual", ctypes.c_uint64)]

            st = _MEM(); st.dwLength = ctypes.sizeof(_MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            inv["ram_gb"] = round(st.ullTotalPhys / 2**30, 1)
        except Exception:  # noqa: BLE001
            pass

    rc, out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                    "--format=csv,noheader"])
    inv["gpu"] = out.splitlines() if rc == 0 else []
    rc, out = _run(["wsl", "-l", "-v"])
    inv["wsl"] = {"cli": shutil.which("wsl") is not None,
                  "distros_probe": "ok" if rc == 0 else f"unavailable rc={rc}"}
    inv["containers"] = {c: (shutil.which(c) is not None) for c in ("docker", "podman", "nerdctl")}
    inv["inference_runtimes"] = {
        c: (shutil.which(c) is not None)
        for c in ("ollama", "lms", "vllm", "llama-server", "localai")
    }
    inv["inference_dirs"] = {
        p: (Path.home() / p).exists() for p in (".ollama", ".cache/lm-studio", ".lmstudio")
    }
    harnesses: dict = {}
    for name, cmd in (("dsh", ["dsh", "--version"]), ("claude", ["claude", "--version"]),
                      ("codex", ["codex", "--version"]), ("gemini", ["gemini", "--version"])):
        rc, out = _run(cmd)
        harnesses[name] = {"cli_on_path": shutil.which(cmd[0]) is not None,
                           "version": out.splitlines()[0] if rc == 0 and out else None}
    harnesses["dsh"]["home_present"] = dsh_home().is_dir()
    inv["harnesses"] = harnesses

    cpa = _discover_cpa_models()
    inv["gateways"] = {
        "cpa": {"port": 8317, "listening": _port_open(8317), "reachable": cpa["reachable"]},
        "cc-switch": {"port": 15721, "listening": _port_open(15721)},
    }
    rc, out = _run(["netstat", "-ano", "-p", "tcp"])
    inv["listening_ports"] = sorted({
        int(m.group(2)) for line in out.splitlines() if "LISTENING" in line
        for m in [re.search(r"(127\.0\.0\.1|0\.0\.0\.0|\[::\]|::):(\d+)\s", line)] if m
    })

    date = _dt.date.today().isoformat()
    dev_dir = INV / "devices"
    dev_dir.mkdir(parents=True, exist_ok=True)
    dev_file = dev_dir / f"{inv['device_id']}.yaml"
    with dev_file.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(inv, fh, allow_unicode=True, sort_keys=False)

    discovered = {"generated_at": inv["generated_at"], "device_id": inv["device_id"],
                  "class": "discovered inventory — pipeline stage Discovered/Reachable only; "
                           "NOT admitted (see registry/models.yaml)",
                  "gateway": "cpa", "reachable": cpa["reachable"],
                  "models": [{"id": m, "stage": "reachable" if cpa["reachable"] else "discovered"}
                             for m in cpa["models"]]}
    disc_file = INV / f"discovered-models-{inv['device_id']}-{date}.json"
    disc_file.write_text(json.dumps(discovered, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"device inventory -> {dev_file}")
    print(f"discovered models ({len(cpa['models'])}) -> {disc_file}")
    return 0


# ---------------------------------------------------------------- governance (Track A)

def cmd_propose_admissions(_args) -> int:
    """Diff latest discovered inventory vs admitted catalog -> proposal (generated).

    Never writes to models.yaml: promotion to Admitted is a human/policy act.
    """
    canonical = load_canonical()
    admitted_ids = {m["id"] for m in canonical["models"]["models"]}
    discovered_files = sorted(INV.glob("discovered-models-*.json"))
    if not discovered_files:
        print("no discovered inventory; run `aic discover` first")
        return 2
    latest = discovered_files[-1]
    discovered = json.loads(latest.read_text(encoding="utf-8"))
    pending = [m["id"] for m in discovered.get("models", []) if m["id"] not in admitted_ids]
    known = [m["id"] for m in discovered.get("models", []) if m["id"] in admitted_ids]
    proposal = INV / f"admission-proposal-{_dt.date.today().isoformat()}.md"
    lines = [
        f"# Admission Proposal {_dt.date.today().isoformat()} (generated, NOT canonical)",
        "",
        f"- source inventory: {latest.name} (stage: discovered/reachable)",
        f"- admitted catalog: {len(admitted_ids)} models (registry/models.yaml)",
        f"- already admitted & discovered: {len(known)}",
        f"- pending discovery-only: {len(pending)}",
        "",
        "## Pending (Discovered, NOT Admitted — require human admission decision)",
        "",
    ]
    lines += [f"- [ ] `{m}`" for m in pending]
    lines += ["", "## Pipeline reminder", "",
              "Discovered ≠ Reachable ≠ Health-checked ≠ Admitted ≠ Routing-enabled.",
              "Health-checked requires runtime canary evidence (not an aic/LLM concern).",
              "Edit registry/models.yaml manually to admit; aic never auto-admits."]
    proposal.write_text("\n".join(lines), encoding="utf-8")
    print(f"admitted={len(admitted_ids)} discovered={len(discovered.get('models', []))} "
          f"pending={len(pending)} -> {proposal}")
    return 0


# ---------------------------------------------------------------- stubs (contract only)

def cmd_not_implemented(args) -> int:
    print(f"aic {args.command}: contract frozen in registry/FREEZE_NOTES.md; "
          "implementation deferred beyond Migration #2 by design.")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(prog="aic", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover")
    p_render = sub.add_parser("render")
    p_render.add_argument("target", choices=["dsh"])
    p_render.add_argument("--out")
    p_diff = sub.add_parser("diff")
    p_diff.add_argument("target", choices=["dsh"])
    sub.add_parser("validate")
    sub.add_parser("propose-admissions")
    for name in ("apply", "bootstrap"):
        sub.add_parser(name)
    args = parser.parse_args()

    if args.command == "discover":
        return cmd_discover(args)
    if args.command == "render":
        return cmd_render(args)
    if args.command == "diff":
        return cmd_diff(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "propose-admissions":
        return cmd_propose_admissions(args)
    return cmd_not_implemented(args)


if __name__ == "__main__":
    sys.exit(main())
