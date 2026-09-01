#!/usr/bin/env python3
"""DSH managed upgrade lifecycle within the existing Personal AI/AIC arch.

Implements the versioned managed-composition model on top of the existing
dsh_runtime composition owner. It is one more tool in the aic package (not a
second updater ecosystem):

  CURRENT_ACCEPTED / PREVIOUS_ACCEPTED / CANDIDATE
  discover -> prepare(build) -> validate(gates) -> propose -> accept -> switch
  rollback restores PREVIOUS_ACCEPTED from local artifacts (no re-download).

The single durable state expression is ~/.dsh/profiles/web/dsh-managed-state.json,
reconstructible from the canonical dsh.yaml runtime_composition contract and the
composition manifest; AIC diff remains the canonical/runtime drift authority.

Constraints honoured:
- AUTO_PRODUCTION_UPGRADE = FORBIDDEN (accept/switch are explicit only).
- Candidate lives in an isolated home; it never touches the live profile.
- The user-facing DSH entry is never switched by this tool.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE))

import aic  # noqa: E402
import dsh_runtime  # noqa: E402

STATE_FILE = "dsh-managed-state.json"
EVIDENCE_NAME = "evidence.jsonl"


# ---------------------------------------------------------------- state

def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))


def state_path(home: Path | None = None) -> Path:
    root = home or dsh_home()
    return root / "profiles" / "web" / STATE_FILE


def load_state(home: Path | None = None) -> dict:
    p = state_path(home)
    if not p.is_file():
        return {"schemaVersion": 1, "current": None, "previous": None,
                "candidate": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise RuntimeError(f"corrupt managed state: {p}")


def save_state(home: Path | None, state: dict) -> Path:
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def ledger(home: Path | None, event: dict) -> Path:
    root = (home or dsh_home()) / ".dsh-lifecycle"
    root.mkdir(parents=True, exist_ok=True)
    ev = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    path = root / EVIDENCE_NAME
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------- contract

def _contract_for(version: str | None) -> dict:
    contract = copy.deepcopy(aic.adapter_contract())
    if version:
        contract["runtime_composition"]["base"]["version"] = version
    return contract


# ---------------------------------------------------------------- discovery

def upstream_discovery() -> dict:
    probe = {"current_installed": _contract_for(None)["runtime_composition"]["base"]["version"],
             "available": None, "dist_tags": None, "probe_error": None}
    try:
        out = subprocess.run(["npm", "view", "@deepseek-ai/dsh", "version",
                              "dist-tags", "--json"], capture_output=True, text=True,
                             timeout=60, encoding="utf-8", errors="replace")
        if out.returncode == 0 and out.stdout.strip():
            data = json.loads(out.stdout)
            probe["available"] = data.get("version")
            probe["dist_tags"] = data.get("dist-tags")
    except Exception as exc:  # noqa: BLE001
        probe["probe_error"] = f"{type(exc).__name__}: {exc}"
    probe["version_delta"] = ("SAME" if probe["available"] == probe["current_installed"]
                              else ("NEWER" if probe["available"] else "UNKNOWN"))
    return probe


# ---------------------------------------------------------------- commands

def cmd_check(args) -> int:
    st = load_state(args.home)
    disc = upstream_discovery()
    print(json.dumps({
        "current": st["current"], "previous": st["previous"],
        "candidate": st["candidate"], "discovery": disc,
        "aic_validate": _run_aic("validate"), "aic_diff_dsh": _run_aic("diff", "dsh"),
    }, ensure_ascii=False, indent=2))
    return 0


def _run_aic(*cmd: str) -> dict:
    try:
        p = subprocess.run([sys.executable, str(aic.ROOT / "scripts" / "aic" / "aic.py"),
                            *cmd], capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        return {"exit": p.returncode, "out": (p.stdout + p.stderr).strip()[-400:]}
    except Exception as exc:  # noqa: BLE001
        return {"exit": -1, "error": str(exc)}


def cmd_prepare(args) -> int:
    version = args.version
    home = Path(args.home) if args.home else dsh_home()
    contract = _contract_for(version)
    cand_root = home / ".dsh-lifecycle" / "candidates"
    cand_root.mkdir(parents=True, exist_ok=True)
    stage = cand_root / f"candidate-{version}-{uuid.uuid4().hex[:8]}"
    try:
        # candidate builds never validate against runtime.lock.yaml pin
        result = dsh_runtime.apply(stage, contract, check_lock=False)
    except dsh_runtime.DshCompositionError as exc:
        ledger(home, {"event": "build_failure", "version": version, "error": str(exc)[-400:]})
        print(f"BUILD_FAILED version={version} error={str(exc)[-400:]}")
        return 1
    manifest = json.loads((stage / "profiles" / "web" / "dsh-runtime-composition.json")
                          .read_text(encoding="utf-8"))
    candidate = {"version": version, "home": str(stage), "stage": str(stage),
                 "compositionHash": result.get("profileCombinationHash"),
                 "nodeVersion": manifest["node"]["version"],
                 "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    st = load_state(home)
    st["candidate"] = candidate
    save_state(home, st)
    ledger(home, {"event": "candidate_built", "version": version,
                  "candidate": candidate["compositionHash"]})
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    return 0


def _node_runtime_for(home: Path, candidate: dict, contract: dict) -> Path:
    node_rel = contract["runtime_composition"]["node"]["relative_to_dsh_home"]
    return home / node_rel / "node.exe"


def cmd_validate(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    st = load_state(home)
    cand = st.get("candidate")
    if not cand:
        print("CANDIDATE_NONE (run prepare first)")
        return 2
    version = cand["version"]
    contract = _contract_for(version)
    cand_home = Path(cand["home"])
    reasons: list[str] = []
    # (a) composition inspect must PASS
    insp = dsh_runtime.inspect(cand_home, contract)
    if insp["status"] != "PASS":
        reasons.append(f"inspect={insp['status']}: "
                       + "; ".join(f"{f['category']}:{f['component']}" for f in
                                   insp["findings"][:8]))
    # (b) node + base smoke
    node = _node_runtime_for(cand_home, cand, contract)
    if not node.is_file():
        reasons.append(f"node missing: {node}")
    # (c) plugins AVAILABLE/LOADABLE/CONFIG
    node_exe = node if node.is_file() else None
    for plugin in contract["runtime_composition"]["managed_rows"]["plugins"]:
        src = aic.ROOT / plugin["source_relative"]
        entry = src / plugin["entry_relative"]
        if not entry.is_file() or not (src / "package.json").is_file():
            reasons.append(f"plugin source missing: {plugin['id']}")
            continue
        deployed = cand_home / "profiles" / "web" / "plugins" / plugin["plugin_directory"] \
            / plugin["entry_relative"]
        if not deployed.is_file():
            reasons.append(f"plugin not deployed: {plugin['id']}")
            continue
        pkg = json.loads((src / "package.json").read_text(encoding="utf-8"))
        if pkg.get("name") != plugin["package"] or pkg.get("version") != plugin["version"]:
            reasons.append(f"plugin identity mismatch: {plugin['id']}")
        if node_exe:
            chk = subprocess.run([str(node_exe), "--check", str(deployed)],
                                 capture_output=True, text=True, timeout=60)
            if chk.returncode != 0:
                reasons.append(f"plugin load syntax fail: {plugin['id']}")
    # (d) behavior regressions (Personal AI pipeline policy suites)
    suites = ["test_dsh_runtime.py", "test_aic.py", "test_aic_apply.py",
              "test_workflow_governance.py", "test_profile_admission.py",
              "test_autonomous_execution_governance.py"]
    regression = {}
    for s in suites:
        p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s",
                            str(aic.ROOT / "tests"), "-p", s],
                           capture_output=True, text=True, timeout=1200,
                           encoding="utf-8", errors="replace")
        regression[s] = p.returncode
        if p.returncode != 0:
            reasons.append(f"regression {s} rc={p.returncode}")
    verdict = "CANDIDATE_VALIDATED" if not reasons else "CANDIDATE_REJECTED"
    cand["verdict"] = verdict
    cand["reasons"] = reasons
    cand["regression"] = regression
    cand["validatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(home, st)
    ledger(home, {"event": "candidate_validation", "version": version,
                  "verdict": verdict, "reasons": reasons[:12],
                  "candidate": cand.get("compositionHash")})
    print(json.dumps({"verdict": verdict, "reasons": reasons, "regression": regression},
                     ensure_ascii=False, indent=2))
    return 0 if verdict == "CANDIDATE_VALIDATED" else 1


def cmd_propose(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    st = load_state(home)
    cand = st.get("candidate")
    if not cand or cand.get("verdict") != "CANDIDATE_VALIDATED":
        print("PROPOSE_BLOCKED (no validated candidate)")
        return 1
    root = home / ".dsh-lifecycle" / "proposals"
    root.mkdir(parents=True, exist_ok=True)
    proposal = {"status": "PROPOSED", "version": cand["version"],
                "candidate": cand.get("compositionHash"),
                "current": st.get("current"),
                "decision": "awaiting_user_cutover",
                "auto_production_upgrade": "FORBIDDEN",
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path = root / f"UPGRADE-{cand['version']}.json"
    path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    ledger(home, {"event": "upgrade_proposal", "version": cand["version"],
                  "proposal": str(path), "candidate": cand.get("compositionHash")})
    print(f"UPGRADE_PROPOSAL=READY path={path}")
    return 0


def cmd_adopt_current(args) -> int:
    """Record the existing live composition as CURRENT_ACCEPTED (init only)."""
    home = Path(args.home) if args.home else dsh_home()
    st = load_state(home)
    if st.get("current"):
        print("STATE_ALREADY_INITIALIZED")
        return 0
    manifest_path = home / "profiles" / "web" / "dsh-runtime-composition.json"
    if not manifest_path.is_file():
        print("NO_COMPOSITION_MANIFEST")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    st["current"] = {
        "version": manifest["base"]["version"],
        "compositionHash": manifest["profileCombinationHash"],
        "compositionId": manifest.get("compositionId"),
        "nodeRelativePath": manifest["node"]["relativePath"],
        "entryRelative": manifest["base"]["entryRelative"],
        "acceptedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_state(home, st)
    ledger(home, {"event": "adopt_current", "version": st["current"]["version"],
                  "compositionHash": st["current"]["compositionHash"]})
    print(json.dumps(st["current"], ensure_ascii=False, indent=2))
    return 0


def cmd_accept(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    st = load_state(home)
    cand = st.get("candidate")
    if not cand or cand.get("verdict") != "CANDIDATE_VALIDATED":
        print("ACCEPT_BLOCKED (validated candidate required)")
        return 1
    new_current = {k: cand[k] for k in ("version", "compositionHash", "nodeVersion")}
    new_current["compositionId"] = cand.get("compositionId", "dsh-context-lifecycle")
    new_current["nodeRelativePath"] = f"runtime/node-{cand['nodeVersion']}-win-x64"
    new_current["entryRelative"] = "profiles/web/base-dsh-" + cand["version"] + \
        "/node_modules/@deepseek-ai/dsh/lib/bin.js"
    new_current["acceptedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st["previous"] = st.get("current")            # A -> previous
    st["current"] = new_current                   # B -> current
    st["candidate"] = None
    save_state(home, st)
    ledger(home, {"event": "accept_switch", "from_version":
                  (st.get("previous") or {}).get("version"),
                  "to_version": new_current["version"],
                  "candidate": cand.get("compositionHash")})
    print(json.dumps({"previous": st.get("previous"), "current": st["current"]},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_rollback(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    st = load_state(home)
    prev = st.get("previous")
    if not prev:
        print("ROLLBACK_NONE (no previous accepted)")
        return 1
    cur = st["current"]
    st["current"], st["previous"] = prev, cur
    save_state(home, st)
    ledger(home, {"event": "rollback_switch", "from_version": cur["version"],
                  "to_version": prev["version"], "rollback_target": prev["version"]})
    print(json.dumps({"current": st["current"], "previous": st["previous"]},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_observe(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    st = load_state(home)
    manifest_path = home / "profiles" / "web" / "dsh-runtime-composition.json"
    report = {
        "MANAGED_LAUNCH": "NOT_OBSERVED", "ACTIVE_COMPOSITION": None,
        "UPSTREAM_VERSION": None, "MANAGED_PLUGINS_LOADED": "NOT_OBSERVED",
        "CONTEXT_RUNTIME": "NOT_OBSERVED", "WORKFLOW_ROUTING": "NOT_OBSERVED",
        "MODEL_PROVENANCE": "NOT_OBSERVED", "AIC_VALIDATE": None,
        "AIC_DIFF_DSH": None, "STARTUP_ERRORS": "NOT_OBSERVED",
        "RUNTIME_FALLBACK": "NOT_OBSERVED",
    }
    if st.get("current"):
        report["ACTIVE_COMPOSITION"] = st["current"].get("compositionHash")
        report["UPSTREAM_VERSION"] = st["current"].get("version")
        report["MANAGED_LAUNCH"] = "DETECTED" if _managed_process(home) else "NOT_RUNNING"
    if manifest_path.is_file():
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        report["ACTIVE_COMPOSITION"] = m.get("profileCombinationHash")
    report["AIC_VALIDATE"] = _run_aic("validate")["exit"]
    report["AIC_DIFF_DSH"] = _run_aic("diff", "dsh")["exit"]
    root = home / ".dsh-lifecycle" / "observations"
    root.mkdir(parents=True, exist_ok=True)
    out = root / f"observe-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    ledger(home, {"event": "post_launch_observation_checkonly", **report})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["AIC_VALIDATE"] == 0 and report["AIC_DIFF_DSH"] == 0 else 1


def _managed_process(home: Path) -> bool:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'profiles\\\\web\\\\base-dsh' } | Measure-Object | Select-Object -ExpandProperty Count"],
                             capture_output=True, text=True, timeout=30,
                             encoding="utf-8", errors="replace")
        return out.stdout.strip().isdigit() and int(out.stdout.strip()) > 0
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dsh_lifecycle",
                                 description="DSH managed upgrade lifecycle (Personal AI/AIC tool family)")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("check", "adopt_current"):
        p = sub.add_parser(name)
        p.add_argument("--home")
    for name in ("prepare", "validate", "propose", "accept", "rollback", "observe"):
        p = sub.add_parser(name)
        p.add_argument("--home")
        p.add_argument("--version")
    args = ap.parse_args(argv)
    fn = globals().get("cmd_" + args.command)
    if not fn:
        ap.error(f"unknown command {args.command}")
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())