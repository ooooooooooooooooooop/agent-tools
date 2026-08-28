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


def collect_opaque_paths(node, prefix: str = "") -> list[str]:
    """Enumerate dotted paths whose value is the <js-expr> opaque marker
    (AIC_OPAQUE_PATH_VISIBILITY closure: every opaque path is explicit)."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(collect_opaque_paths(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            ident = v.get("id") if isinstance(v, dict) and v.get("id") else i
            out.extend(collect_opaque_paths(v, f"{prefix}[{ident}]"))
    elif node == "<js-expr>":
        out.append(prefix)
    return out


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
    if args.target != "dsh":
        print(yaml.safe_dump(render_harness(args.target), allow_unicode=True, sort_keys=False))
        return 0
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
    if args.target != "dsh":
        rows = diff_harness(args.target)
        bad = [r for r in rows if not r["ok"]]
        if not bad:
            print("NO DRIFT")
            return 0
        print("DRIFT detected:")
        for r in bad:
            print(f"  file={r['file']}\n    field={r['field']}\n"
                  f"    expected={r['expected']!r}\n    actual={r['actual']!r}")
        return 1
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

    # AIC_OPAQUE_PATH_VISIBILITY closure: enumerate every opaque cordis !!js path,
    # compare against the declared contract list; silent growth = drift.
    opaque_found: list[str] = []
    cordis_path_str = ""
    for target in contract["render_targets"]:
        if target["id"] == "agent-preset-cc":
            actual_path = Path(target["path"].replace("{{DSH_HOME}}", str(dsh_home())))
            cordis_path_str = target["path"]
            if actual_path.is_file():
                opaque_found = collect_opaque_paths(load_cordis_yaml(actual_path))
    declared = {o["path"] for o in contract.get("opaque_paths", [])}
    undeclared = sorted(set(opaque_found) - declared)
    for p in undeclared:
        findings.append((cordis_path_str, f"<opaque>{p}", "declared in contract", "undeclared"))
    print(f"[metadata] opaque_paths ({len(opaque_found)}): "
          + (", ".join(sorted(opaque_found)) if opaque_found else "none"))

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

    # Migration #5: multi-harness contract validation
    admitted_ids = {m["id"] for m in models}
    pgw = load_private_gateways()
    for hname in ("codex", "claude", "gemini", "switchboard"):
        try:
            hc = harness_contract(hname)
            for field in ("render_targets", "overlay", "static_instructions",
                          "runtime_context_hook", "health_check", "consumes"):
                if field not in hc:
                    errors.append(f"harnesses/{hname}.yaml: missing contract field '{field}'")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"harnesses/{hname}.yaml: unreadable: {exc}")
            continue
        for target in hc.get("render_targets", []):
            for fspec in target.get("generated_fields", []):
                try:
                    resolve_expected(fspec, canonical, hc, pgw)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"harnesses/{hname}.yaml: unresolvable {fspec.get('path')}: {exc}")

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


# ---------------------------------------------------------------- generic harness adapters (Migration #5)
# render/diff for codex/claude/gemini/switchboard. Field-level projection only:
# aic renders the GENERATED field projection; overlay/runtime/secret stay
# user-owned. TARGET BEHAVIOR = CURRENT BEHAVIOR (diff must be NO DRIFT).

PRIVATE_STATE = Path(os.environ.get("PERSONAL_AI_STATE", Path.home() / "personal-ai-state"))
INSTRUCTION_SYNC_GROUP = ("codex", "claude", "gemini")


def load_private_gateways() -> dict:
    p = PRIVATE_STATE / "registry" / "gateways.yaml"
    return load_yaml(p) if p.is_file() else {"gateways": {}}


def harness_contract(name: str) -> dict:
    return load_yaml(REG / "harnesses" / f"{name}.yaml")


def _dig(node, dotted: str):
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def resolve_expected(spec: dict, canonical: dict, harness: dict, pgw: dict):
    src = spec["source"]
    if src == "literal":
        return spec.get("value")
    if src == "harness_defaults.model":
        return harness.get("harness_defaults", {}).get("model")
    if src == "harness_defaults.model_alias":
        return harness.get("harness_defaults", {}).get("model_alias")
    if src == "capabilities.mcp_standard":
        caps = load_yaml(REG / "capabilities.yaml")
        return set(caps["capabilities"]["mcp_standard"].keys())
    if src.startswith("gateways."):
        rest = src[len("gateways."):]
        if rest.endswith(".listen_url"):
            return "http://" + pgw["gateways"][rest[:-len(".listen_url")]]["listen"]
        if rest.endswith(".alias_map"):
            amap = pgw["gateways"][rest[:-len(".alias_map")]]["alias_map"]
            return amap[spec["key"]] if spec.get("side") == "actual" else spec["key"]
    if src == "routing-policy.broker_preferences":
        return {k: v for k, v in canonical["policy"]["rules"]["broker_preferences"].items()
                if k != "evidence"}
    if src.startswith("models.admitted"):
        provider = src[src.find("provider=") + 9:].rstrip(")")
        return {m["id"].split("/", 1)[1] for m in canonical["models"]["models"]
                if m["id"].startswith(provider + "/")}
    raise ValueError(f"unknown source: {src}")


def compare_field(mode: str, expected, actual) -> bool:
    if mode == "exact":
        return expected == actual
    if mode == "superset_keys":
        return isinstance(actual, dict) and set(actual.keys()) >= set(expected)
    if mode == "subset":
        return isinstance(actual, list) and set(actual) <= set(expected)
    if mode == "exact_subset_map":
        return isinstance(actual, dict) and all(
            actual.get(k) == v for k, v in expected.items())
    raise ValueError(f"unknown mode: {mode}")


def diff_harness(name: str) -> list:
    canonical = load_canonical()
    harness = harness_contract(name)
    pgw = load_private_gateways()
    base = Path.home() / harness["home"]
    rows = []
    for target in harness.get("render_targets", []):
        fname = target["file"]
        path = base / fname
        check = target.get("check")
        if check:
            mode = check["mode"]
            if mode == "exists":
                rows.append({"file": fname, "field": "<exists>", "expected": "present",
                             "actual": "present" if path.is_file() else "MISSING",
                             "ok": path.is_file()})
            elif mode == "managed_block_marker":
                text = path.read_text(encoding="utf-8-sig", errors="replace") if path.is_file() else ""
                ok = check["marker"] in text
                rows.append({"file": fname, "field": "<managed-block>",
                             "expected": check["marker"],
                             "actual": "present" if ok else "MISSING", "ok": ok})
            continue
        data = None
        if path.is_file():
            fmt = target.get("format")
            if fmt == "json":
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            elif fmt == "toml":
                import tomllib
                data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        if data is None:
            rows.append({"file": fname, "field": "<file>", "expected": "present",
                         "actual": "MISSING", "ok": False})
            continue
        for fspec in target.get("generated_fields", []):
            expected = resolve_expected(fspec, canonical, harness, pgw)
            actual = _dig(data, fspec["path"])
            mode = fspec.get("mode", "exact")
            ok = compare_field(mode, expected, actual)
            rows.append({"file": fname, "field": fspec["path"],
                         "expected": sorted(expected) if isinstance(expected, (set, list)) else expected,
                         "actual": actual if not isinstance(actual, dict) else sorted(actual.keys()),
                         "ok": ok})
    # instruction sync-group check (codex/claude/gemini must stay identical)
    if name in INSTRUCTION_SYNC_GROUP:
        import hashlib
        hashes = {}
        for other in INSTRUCTION_SYNC_GROUP:
            oh = harness_contract(other)
            instr = [t for t in oh.get("render_targets", [])
                     if t.get("kind") == "instruction"]
            if instr:
                p = Path.home() / oh["home"] / instr[0]["file"]
                hashes[other] = hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.is_file() else "MISSING"
        ok = len(set(hashes.values())) == 1
        rows.append({"file": "instructions", "field": "<sync-group>",
                     "expected": "identical across codex/claude/gemini",
                     "actual": json.dumps(hashes), "ok": ok})
    return rows


def render_harness(name: str) -> dict:
    canonical = load_canonical()
    harness = harness_contract(name)
    pgw = load_private_gateways()
    projection = {"harness": name, "home": harness["home"], "targets": []}
    for target in harness.get("render_targets", []):
        entry = {"file": target["file"]}
        if target.get("check"):
            entry["check"] = target["check"]["mode"]
            entry["owner"] = target.get("owner", "user")
        else:
            entry["generated"] = {f["path"]: (sorted(v) if isinstance(v := resolve_expected(
                f, canonical, harness, pgw), (set, list)) else v)
                for f in target.get("generated_fields", [])}
            entry["overlay"] = "preserved (not rendered)"
        projection["targets"].append(entry)
    return projection


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


# ---------------------------------------------------------------- apply
# Runtime Closure: apply <target> 把 canonical+adapter+device reality+overlay
# 渲染出的 generated state 安全应用到实际 Harness。
# 权限边界：只能写 adapter 契约声明为 GENERATED 的字段/文件段；
# OVERLAY / MACHINE_LOCAL / RUNTIME_STATE / SECRET / 用户手写文件一律不碰。

APPLY_BACKUPS = INV / "apply-backups"          # machine-local，gitignored
DSH_GENERATED_SECTIONS = ("llm-pi-ai", "agent-default-model")  # settings.yaml 可写段


def _apply_backup(target: str, path: Path) -> Path | None:
    if not path.is_file():
        return None
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = APPLY_BACKUPS / f"{target}-{ts}" / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return dest


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".aic-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _set_dotted(data: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _toml_format_value(v) -> str:
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_format_value(x) for x in v) + "]"
    raise ValueError(f"toml unsupported value: {v!r}")


def _toml_set_top_scalar(text: str, key: str, value) -> str:
    """外科手术式替换顶层标量行（保留注释/格式/其余字节）。"""
    pat = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
    line = f"{key} = {_toml_format_value(value)}"
    if pat.search(text):
        return pat.sub(line, text, count=1)
    return text.rstrip("\n") + "\n" + line + "\n"


def _toml_append_table(text: str, table: str, spec: dict) -> str:
    lines = [f"[{table}]"]
    for k, v in spec.items():
        lines.append(f"{k} = {_toml_format_value(v)}")
    return text.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n"


def _cordis_set_scalar(text: str, row_id: str, dotted: str, value) -> str | None:
    """在 cordis yml 原文中外科手术式设置 row 内嵌套标量，保留 !!js 等全部其他字节。

    返回新文本；定位失败/歧义返回 None（调用方转 REVIEW，不写部分状态）。
    """
    lines = text.splitlines(keepends=True)
    row_idx = row_indent = None
    for i, ln in enumerate(lines):
        m = re.match(rf"^(\s*)-?\s*id:\s*{re.escape(row_id)}\s*$", ln)
        if m:
            if row_idx is not None:
                return None                       # 歧义
            row_idx, row_indent = i, len(m.group(1))
    if row_idx is None:
        return None
    parts = dotted.split(".")
    stack: list[tuple[int, str]] = []             # (indent, key) 路径栈
    for i in range(row_idx + 1, len(lines)):
        m = re.match(r"^(\s*)([^\s:#][^:]*):\s*(.*?)\s*$", ln := lines[i])
        if not m:
            if ln.strip() and not ln.strip().startswith("#") and \
                    len(ln) - len(ln.lstrip()) <= (row_indent or 0):
                break                             # 离开 row 块
            continue
        indent, key, val = len(m.group(1)), m.group(2).strip(), m.group(3)
        if indent <= (row_indent or 0):
            break
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = [k for _, k in stack] + [key]
        if path == parts:
            if val.startswith("!!"):
                return None                       # opaque 字段绝不改写
            newline = "\r\n" if ln.endswith("\r\n") else "\n"
            lines[i] = f"{' ' * indent}{key}: {value}{newline}"
            return "".join(lines)
        stack.append((indent, key))
    return None


def _applyable_superset_values(fspec: dict):
    """superset_keys apply 需要完整值：目前仅 capabilities.mcp_standard 可机械推导。"""
    if fspec["source"] == "capabilities.mcp_standard":
        return load_yaml(REG / "capabilities.yaml")["capabilities"]["mcp_standard"]
    return None


def _classify_drift(name: str, harness: dict, drift: list) -> tuple[dict, set, list, list]:
    """把 diff rows 分类：writable（generated_fields）/ creatable（缺文件可新建）/
    hard（非 generated → REVIEW）/ soft（check-mode：exists/marker/secret → 警告不阻塞）。

    返回 (writable, creatable, hard, soft)。§5/§12：secret/instruction 缺失不阻塞
    无关 generated config 的安全 apply。
    """
    writable: dict[str, dict] = {}
    creatable: set[str] = set()
    check_files: set[str] = set()
    for target in harness.get("render_targets", []):
        if target.get("check"):
            check_files.add(target["file"])
            continue
        creatable.add(target["file"])
        for f in target.get("generated_fields", []):
            writable[f"{target['file']}::{f['path']}"] = {**f, "file": target["file"],
                                                          "format": target.get("format")}
    hard, soft = [], []
    for r in drift:
        key = f"{r['file']}::{r['field']}"
        if key in writable or (r["field"] == "<file>" and r["file"] in creatable):
            continue
        if r["file"] in check_files or r["field"] == "<sync-group>":
            soft.append(r)
        else:
            hard.append(r)
    return writable, creatable, hard, soft


def _apply_generic(name: str) -> tuple[int, list[str]]:
    """通用 harness apply。返回 (exit_code, messages)。

    exit: 0 OK/NO DRIFT, 1 REVIEW_REQUIRED, 3 FAIL_ROLLED_BACK, 4 OPTIONAL_NOT_INSTALLED
    """
    canonical = load_canonical()
    harness = harness_contract(name)
    pgw = load_private_gateways()
    home = Path.home() / harness["home"]
    if not home.is_dir():
        print(f"APPLY {name}: OPTIONAL_NOT_INSTALLED（{harness['home']} 不存在，跳过）")
        return 4, []
    rows = diff_harness(name)
    drift = [r for r in rows if not r["ok"]]
    if not drift:
        print(f"APPLY {name}: NO DRIFT")
        return 0, []

    writable, creatable, hard, soft = _classify_drift(name, harness, drift)
    msgs: list[str] = []
    for r in soft:
        print(f"APPLY {name}: NOTE — 非 aic 所有权 drift 保留给 owner: "
              f"{r['file']}::{r['field']}（不阻塞 generated apply）")
    if hard:
        for r in hard:
            print(f"APPLY {name}: REVIEW_REQUIRED — 非 generated drift "
                  f"file={r['file']} field={r['field']}（OVERLAY/UNKNOWN 不写）")
        return 1, msgs
    # 只处理 writable drift（soft 已报告、hard 已拦截）
    by_file: dict[str, list] = {}
    for r in drift:
        if f"{r['file']}::{r['field']}" in writable or \
                (r["field"] == "<file>" and r["file"] in creatable):
            by_file.setdefault(r["file"], []).append(r)
    if not by_file:
        print(f"APPLY {name}: 无可写 generated drift（仅 owner-owned 余项）")
        return 0, msgs
    changed_files = []
    for fname, fdrift in by_file.items():
        path = home / fname
        file_missing = any(r["field"] == "<file>" for r in fdrift)
        # 文件缺失时补全该文件全部 generated 字段；否则只写 drift 字段
        want = {f["path"]: f for f in harness["render_targets"]
                if f.get("file") == fname for f in f.get("generated_fields", [])}
        if not file_missing:
            want = {r["field"]: writable[f"{fname}::{r['field']}"] for r in fdrift}
        fmt = next(iter(want.values()), {}).get("format", "json") if want else "json"
        text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        new_text = text
        if fmt == "json":
            data = json.loads(text) if text.strip() else {}
            for fpath, fspec in want.items():
                expected = resolve_expected(fspec, canonical, harness, pgw)
                mode = fspec.get("mode", "exact")
                if mode in ("exact", "exact_subset_map"):
                    if mode == "exact_subset_map" and fname.endswith("config.json"):
                        cur = _dig(data, fpath)
                        if not isinstance(cur, dict):
                            cur = {}
                        cur.update(expected)
                        _set_dotted(data, fpath, cur)
                    else:
                        _set_dotted(data, fpath, expected)
                elif mode == "superset_keys":
                    vals = _applyable_superset_values(fspec)
                    if vals is None:
                        print(f"APPLY {name}: REVIEW_REQUIRED — superset 无机械推导值 "
                              f"{fpath}")
                        return 1, msgs
                    cur = _dig(data, fpath)
                    if not isinstance(cur, dict):
                        cur = {}
                    for k in expected:
                        if k not in cur:
                            cur[k] = vals[k]
                    _set_dotted(data, fpath, cur)
                else:
                    print(f"APPLY {name}: REVIEW_REQUIRED — mode={mode} 不自动写")
                    return 1, msgs
            new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        elif fmt == "toml":
            for fpath, fspec in want.items():
                expected = resolve_expected(fspec, canonical, harness, pgw)
                mode = fspec.get("mode", "exact")
                if mode == "exact" and "." not in fpath:
                    new_text = _toml_set_top_scalar(new_text, fpath, expected)
                elif mode == "superset_keys":
                    vals = _applyable_superset_values(fspec)
                    if vals is None:
                        print(f"APPLY {name}: REVIEW_REQUIRED — superset 无机械推导值")
                        return 1, msgs
                    import tomllib
                    cur = (tomllib.loads(new_text).get(fpath, {})
                           if new_text.strip() else {})
                    for k in expected:
                        if k not in cur:
                            new_text = _toml_append_table(
                                new_text, f"{fpath}.{k}", vals[k])
                else:
                    print(f"APPLY {name}: REVIEW_REQUIRED — toml 嵌套/mode={mode} 不自动写")
                    return 1, msgs
        backup = _apply_backup(name, path)
        _atomic_write_text(path, new_text)
        changed_files.append((fname, backup))
        msgs.append(f"applied {fname}: {sorted(want)}")
    # post-apply diff：writable 字段必须全修好（soft 余项允许存在）；否则回滚
    bad = [r for r in diff_harness(name) if not r["ok"]]
    _, _, hard_post, _soft_post = _classify_drift(name, harness, bad)
    writable_left = [r for r in bad
                     if f"{r['file']}::{r['field']}" in writable]
    if writable_left or hard_post:
        for fname, backup in changed_files:
            if backup:
                shutil.copy2(backup, home / fname)
            else:
                (home / fname).unlink(missing_ok=True)   # apply 新建的文件回滚即删除
        print(f"APPLY {name}: FAIL_ROLLED_BACK — post-diff 仍有 drift "
              f"{[r['field'] for r in writable_left + hard_post]}，before snapshot 已恢复")
        return 3, msgs
    print(f"APPLY {name}: OK ({sum(len(v) for v in by_file.values())} field(s), "
          f"post-diff NO DRIFT)")
    return 0, msgs


def _dsh_structured_drift() -> dict:
    """结构化 dsh drift：full-file / cordis field / opaque / missing 分类。"""
    canonical = load_canonical()
    overlay = adapter_overlay()
    contract = adapter_contract()
    out = {"full_file": [], "cordis": [], "cordis_missing": False,
           "opaque_undeclared": [], "settings_missing": False}
    for target in contract["render_targets"]:
        actual_path = Path(target["path"].replace("{{DSH_HOME}}", str(dsh_home())))
        if not actual_path.is_file():
            if target["mode"] == "full-file":
                out["settings_missing"] = True
            else:
                out["cordis_missing"] = True
            continue
        if target["mode"] == "full-file":
            actual = load_yaml(actual_path)
            deep_diff(render_settings(canonical, overlay), actual, "", out["full_file"])
        elif target["mode"] == "field-projection":
            actual = load_cordis_yaml(actual_path)
            for check in target.get("field_checks", []):
                row = find_row(actual, check["row"])
                act = get_nested(row, check["key"]) if row is not None else _MISSING
                exp = resolve_value_from(canonical["policy"], check["value_from"])
                if act != exp:
                    out["cordis"].append((check, exp, act))
            declared = {o["path"] for o in contract.get("opaque_paths", [])}
            out["opaque_undeclared"] = sorted(set(collect_opaque_paths(actual)) - declared)
    return out


def _apply_dsh() -> tuple[int, list[str]]:
    canonical = load_canonical()
    overlay = adapter_overlay()
    contract = adapter_contract()
    d = _dsh_structured_drift()
    if not any([d["full_file"], d["cordis"], d["cordis_missing"],
                d["opaque_undeclared"], d["settings_missing"]]):
        print("APPLY dsh: NO DRIFT")
        return 0, []
    msgs: list[str] = []
    if d["opaque_undeclared"]:
        # AIC_OPAQUE_PATH_VISIBILITY：silent growth 必须人工登记，绝不自动写
        print(f"APPLY dsh: REVIEW_REQUIRED — 未登记 opaque path: {d['opaque_undeclared']}")
        return 1, msgs
    if d["cordis_missing"]:
        print("APPLY dsh: NOTE — agent-preset-cc 缺失（preset 结构属 harness overlay，"
              "不自动重建；不阻塞 settings 修复）")
    written: list[tuple[Path, Path | None]] = []
    for target in contract["render_targets"]:
        actual_path = Path(target["path"].replace("{{DSH_HOME}}", str(dsh_home())))
        if target["mode"] == "full-file" and (d["full_file"] or d["settings_missing"]):
            if d["settings_missing"] and not dsh_home().is_dir():
                print("APPLY dsh: OPTIONAL_NOT_INSTALLED")
                return 4, msgs
            bad = [f for f, _, _ in d["full_file"]
                   if f.split(".")[0].split("[")[0] not in DSH_GENERATED_SECTIONS]
            if bad:
                print(f"APPLY dsh: REVIEW_REQUIRED — 非 generated 段 drift: {bad}")
                return 1, msgs
            backup = _apply_backup("dsh", actual_path)
            _atomic_write_text(actual_path, yaml.safe_dump(
                render_settings(canonical, overlay), allow_unicode=True, sort_keys=False))
            written.append((actual_path, backup))
            msgs.append(f"applied settings: {[f for f, _, _ in d['full_file']] or ['<file>']}")
        elif target["mode"] == "field-projection" and d["cordis"]:
            text = actual_path.read_text(encoding="utf-8-sig")
            new_text = text
            applied = []
            for check, exp, _act in d["cordis"]:
                new2 = _cordis_set_scalar(new_text, check["row"], check["key"], exp)
                if new2 is None:
                    print(f"APPLY dsh: REVIEW_REQUIRED — cordis 定位失败/歧义/opaque "
                          f"{check['row']}.{check['key']}（不写部分状态）")
                    return 1, msgs
                new_text = new2
                applied.append(f"{check['row']}.{check['key']}")
            backup = _apply_backup("dsh", actual_path)
            _atomic_write_text(actual_path, new_text)
            written.append((actual_path, backup))
            msgs.append(f"applied cordis: {applied}")
    if not written:
        print("APPLY dsh: 无可写 generated drift")
        return 0, msgs
    # post-apply：full_file/cordis 必须清零；cordis_missing 属 soft 余项允许存在
    post = _dsh_structured_drift()
    if post["full_file"] or post["cordis"] or post["opaque_undeclared"]:
        for live, backup in written:
            if backup:
                shutil.copy2(backup, live)
            else:
                live.unlink(missing_ok=True)
        print("APPLY dsh: FAIL_ROLLED_BACK — post-diff 仍有 drift，before snapshot 已恢复")
        return 3, msgs
    print("APPLY dsh: OK (post-diff generated NO DRIFT)")
    return 0, msgs


def cmd_apply(args) -> int:
    """apply <target>：validate → discover（缺 inventory 时）→ render → diff →
    ownership classify → 全部 generated 才写；snapshot + atomic + post-diff + rollback。"""
    target = getattr(args, "target", None)
    if not target:
        print("usage: aic apply <dsh|codex|claude|gemini|switchboard>")
        return 2
    # Precondition 1: canonical valid
    import io
    buf = io.StringIO()
    _stdout = sys.stdout
    sys.stdout = buf
    try:
        vrc = cmd_validate(args)
    finally:
        sys.stdout = _stdout
    if vrc != 0:
        print("APPLY: BLOCKED — canonical INVALID（先修复 registry）")
        print(buf.getvalue())
        return 2
    # Precondition 2: device inventory（缺失才 discover，避免每次网络探测）
    dev = INV / "devices" / f"{platform.node() or getpass.getuser()}.yaml"
    if not dev.is_file():
        cmd_discover(args)
    if target == "dsh":
        rc, _ = _apply_dsh()
    else:
        rc, _ = _apply_generic(target)
    return rc if rc != 4 else 0     # OPTIONAL_NOT_INSTALLED 不算失败


# ---------------------------------------------------------------- stubs (contract only)

def cmd_not_implemented(args) -> int:
    print(f"aic {args.command}: contract frozen in registry/FREEZE_NOTES.md; "
          "implementation deferred beyond Migration #2 by design.")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(prog="aic", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_disc = sub.add_parser("discover")
    p_disc.add_argument("--propose-admissions", action="store_true")
    p_render = sub.add_parser("render")
    p_render.add_argument("target", choices=["dsh", "codex", "claude", "gemini", "switchboard"])
    p_render.add_argument("--out")
    p_diff = sub.add_parser("diff")
    p_diff.add_argument("target", choices=["dsh", "codex", "claude", "gemini", "switchboard"])
    sub.add_parser("validate")
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("target",
                         choices=["dsh", "codex", "claude", "gemini", "switchboard"])

    sub.add_parser("bootstrap")
    args = parser.parse_args()

    if args.command == "discover":
        rc = cmd_discover(args)
        if rc == 0 and getattr(args, "propose_admissions", False):
            return cmd_propose_admissions(args)
        return rc
    if args.command == "render":
        return cmd_render(args)
    if args.command == "diff":
        return cmd_diff(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "apply":
        return cmd_apply(args)
    return cmd_not_implemented(args)


if __name__ == "__main__":
    sys.exit(main())
