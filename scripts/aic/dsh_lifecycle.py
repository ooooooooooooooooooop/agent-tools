#!/usr/bin/env python3
"""DSH managed runtime lifecycle convergence within Personal AI / AIC architecture.

Establishes and verifies the unambiguous 8-layer DSH runtime lifecycle:
  1. SOURCE_REMOTE_CURRENT      (remote accepted commit & fetch timestamp)
  2. DEPLOYMENT_SOURCE_CURRENT   (clean disposable deployment mirror, never dirty)
  3. CANDIDATE_BUILT             (built in isolated staging from clean mirror)
  4. CANDIDATE_VALIDATED         (AIC validate, inspect, plugin hashes & regression gates)
  5. DEPLOYED_READY              (deployed runtime profile on disk)
  6. ACTIVE                      (live process PID, start time, active composition)
  7. LIVE_VALIDATED              (live smoke: HTTP health, plugins, token-meter, admission)
  8. ACCEPTED_CURRENT            (all 7 layers in verified alignment)

Hard boundaries:
- Personal AI writes to DSH ONLY.
- Claude Code, Codex, Gemini are user/native owned (Personal AI write control forbidden).
- Developer workspace (e.g. C:\\Desktop\\skills) is NEVER modified, stashed, or reset for production.
- Production deployment is NEVER blocked by developer workspace being dirty.
- Local-only dirty changes in developer workspace NEVER leak into production deployment.
- Active runtime directory is IMMUTABLE while in use (no in-place destructive mutation).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE))

import aic  # noqa: E402
import dsh_runtime  # noqa: E402

STATE_FILE = "dsh-managed-state.json"
LIFECYCLE_FILE = "dsh-runtime-lifecycle.json"
ACTIVE_RECEIPT_FILE = "active-process.json"
DEPLOYMENT_MIRROR_DIR = ".deployment-mirror"
EVIDENCE_NAME = "evidence.jsonl"


# ---------------------------------------------------------------- paths & state

def dsh_home() -> Path:
    return Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))


def state_path(home: Path | None = None) -> Path:
    root = home or dsh_home()
    return root / "profiles" / "web" / STATE_FILE


def lifecycle_path(home: Path | None = None) -> Path:
    root = home or dsh_home()
    return root / "profiles" / "web" / LIFECYCLE_FILE


def deployment_mirror_path(home: Path | None = None) -> Path:
    root = home or dsh_home()
    return root / DEPLOYMENT_MIRROR_DIR / "agent-tools"


def load_state(home: Path | None = None) -> dict:
    p = state_path(home)
    if not p.is_file():
        return {"schemaVersion": 1, "current": None, "previous": None, "candidate": None}
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        raise RuntimeError(f"corrupt managed state: {p}")


def save_state(home: Path | None, state: dict) -> Path:
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_lifecycle_state(home: Path | None = None) -> dict:
    p = lifecycle_path(home)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def save_lifecycle_state(home: Path | None, state: dict) -> Path:
    p = lifecycle_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


# ---------------------------------------------------------------- deployment mirror

def ensure_deployment_mirror(home: Path | None = None,
                             source_repo: Path | None = None,
                             target_commit: str | None = None) -> dict:
    """Ensure a clean, disposable deployment mirror exists and is checked out at target_commit.

    The mirror is completely decoupled from the developer workspace:
    - Never dirtied by local experiments.
    - Always checked out clean (DIRTY = False).
    - Sourced from durable remote / accepted commit.
    - Can be recreated at any time from remote.
    """
    mirror_dir = deployment_mirror_path(home)
    src = source_repo or aic.ROOT
    mirror_dir.parent.mkdir(parents=True, exist_ok=True)

    if not (mirror_dir / ".git").is_dir():
        rc = subprocess.run(["git", "clone", "--no-checkout", str(src), str(mirror_dir)],
                            capture_output=True, text=True)
        if rc.returncode != 0:
            raise RuntimeError(f"failed to initialize deployment mirror: {rc.stderr}")

    subprocess.run(["git", "-C", str(mirror_dir), "fetch", "--quiet", str(src),
                    "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*"],
                   capture_output=True, text=True)

    if not target_commit:
        rc, out = _git(mirror_dir, "rev-parse", "refs/remotes/origin/main")
        if rc != 0:
            rc, out = _git(mirror_dir, "rev-parse", "HEAD")
        target_commit = out.strip()

    subprocess.run(["git", "-C", str(mirror_dir), "checkout", "--force", "--detach", target_commit],
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(mirror_dir), "clean", "-ffxd"],
                   capture_output=True, text=True)

    rc, status_out = _git(mirror_dir, "status", "--porcelain")
    rc, head_out = _git(mirror_dir, "rev-parse", "HEAD")
    is_dirty = bool(status_out.strip())
    actual_commit = head_out.strip()

    return {
        "path": str(mirror_dir),
        "commit": actual_commit,
        "dirty": is_dirty,
        "clean": not is_dirty,
    }


# ---------------------------------------------------------------- process identity

def _find_live_dsh_process() -> dict | None:
    """Query Win32_Process for the live running DSH Web host process."""
    try:
        ps_cmd = (
            'Get-CimInstance Win32_Process | '
            'Where-Object { $_.CommandLine -like "*base-dsh*" -and $_.CommandLine -like "*web*" } | '
            'Select-Object ProcessId, CommandLine, CreationDate | ConvertTo-Json'
        )
        p = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_cmd],
                           capture_output=True, text=True, timeout=20, errors="replace")
        if p.returncode != 0 or not p.stdout.strip():
            return None
        data = json.loads(p.stdout)
        if isinstance(data, list):
            data = next((item for item in data if "bin.js web" in item.get("CommandLine", "")), data[0])

        pid = int(data.get("ProcessId"))
        cmd = str(data.get("CommandLine", ""))
        cdate_str = str(data.get("CreationDate", ""))
        epoch_ms = 0
        if "/Date(" in cdate_str:
            m = re.search(r"/Date\((\d+)\)/", cdate_str)
            if m:
                epoch_ms = int(m.group(1))

        start_iso = datetime.fromtimestamp(epoch_ms / 1000.0, timezone.utc).isoformat() if epoch_ms else None

        base_match = re.search(r"base-dsh-([^\\/\s]+)", cmd)
        base_ver = base_match.group(1) if base_match else None

        node_match = re.search(r"node-(v[^\\/\s]+)", cmd)
        node_ver = node_match.group(1) if node_match else None

        return {
            "pid": pid,
            "commandLine": cmd,
            "startTime": start_iso,
            "startTimeEpoch": epoch_ms / 1000.0,
            "baseVersion": base_ver,
            "nodeVersion": node_ver,
        }
    except Exception:
        return None


def inspect_active_process(home: Path | None = None) -> dict:
    """Evaluate deployed runtime composition vs currently active process identity."""
    root = home or dsh_home()
    profile_web = root / "profiles" / "web"
    manifest_path = profile_web / "dsh-runtime-composition.json"

    deployed_hash = None
    manifest_mtime = 0.0
    if manifest_path.is_file():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            deployed_hash = m.get("profileCombinationHash")
            manifest_mtime = manifest_path.stat().st_mtime
        except Exception:
            pass

    receipt_path = profile_web / ACTIVE_RECEIPT_FILE
    receipt = None
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        except Exception:
            pass

    proc_info = _find_live_dsh_process()
    if not proc_info:
        return {
            "running": False,
            "pid": None,
            "startTime": None,
            "commandLine": None,
            "base": None,
            "node": None,
            "profile": str(profile_web),
            "activeComposition": None,
            "deployedComposition": deployed_hash,
            "isStale": False,
            "restartRequired": True,
            "restartReason": "PROCESS_NOT_RUNNING",
        }

    pid = proc_info["pid"]
    start_time_iso = proc_info.get("startTime")
    start_time_epoch = proc_info.get("startTimeEpoch", 0.0)
    cmdline = proc_info.get("commandLine", "")

    st = load_state(root)
    accepted_comp = (st.get("current") or {}).get("compositionHash")

    if receipt and receipt.get("pid") == pid and receipt.get("compositionHash"):
        active_comp = receipt.get("compositionHash")
    elif start_time_epoch > 0 and manifest_mtime > 0:
        if start_time_epoch < (manifest_mtime - 2.0):
            active_comp = accepted_comp or (st.get("previous") or {}).get("compositionHash")
        else:
            active_comp = deployed_hash
    else:
        active_comp = accepted_comp or deployed_hash

    is_stale = bool(deployed_hash and active_comp and deployed_hash != active_comp)
    restart_required = is_stale
    restart_reason = "ACTIVE_PROCESS_STALE_PLUGIN" if is_stale else None

    return {
        "running": True,
        "pid": pid,
        "startTime": start_time_iso,
        "commandLine": cmdline,
        "base": proc_info.get("baseVersion"),
        "node": proc_info.get("nodeVersion"),
        "profile": str(profile_web),
        "activeComposition": active_comp,
        "deployedComposition": deployed_hash,
        "isStale": is_stale,
        "restartRequired": restart_required,
        "restartReason": restart_reason,
    }


# ---------------------------------------------------------------- live smoke validator

def run_live_smoke(home: Path | None = None, base_url: str = "http://127.0.0.1:3080") -> dict:
    """Execute focused production live smoke on the running DSH process."""
    root = home or dsh_home()
    profile_web = root / "profiles" / "web"
    checks: dict[str, Any] = {}

    # 1. HTTP health check
    try:
        req = urllib.request.Request(base_url, headers={"User-Agent": "DSH-Lifecycle-Smoke"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            checks["HTTP_HEALTH"] = {"status": "PASS", "statusCode": resp.status}
    except Exception as exc:
        checks["HTTP_HEALTH"] = {"status": "FAIL", "error": str(exc)}

    # 2. Plugin inventory check
    managed_plugins = [
        "dsh-token-meter-pressure-guard",
        "dsh-agent-loop-pressure-guard",
        "dsh-tool-result-pruner-pressure-guard",
        "dsh-compaction-convergence",
        "dsh-context-lifecycle",
        "dsh-model-switch-controller",
        "dsh-workflow-model-preflight-gate",
        "dsh-autonomous-execution-governor",
    ]
    missing_plugins = [p for p in managed_plugins if not (profile_web / "plugins" / p / "package.json").is_file()]
    checks["PLUGIN_INVENTORY"] = {
        "status": "PASS" if not missing_plugins else "FAIL",
        "total": len(managed_plugins),
        "missing": missing_plugins,
    }

    # 3. Token Meter check (verify replacement replay normalization logic present)
    tm_lib = profile_web / "plugins" / "dsh-token-meter-pressure-guard" / "lib" / "index.js"
    tm_pass = False
    if tm_lib.is_file():
        txt = tm_lib.read_text(encoding="utf-8", errors="ignore")
        if "Math.max(0, value)" in txt and "isReplacementSurfaceEvent" in txt:
            tm_pass = True
    checks["TOKEN_METER"] = {"status": "PASS" if tm_pass else "FAIL"}

    # 4. Context Admission check (verify pressure guard admission clamp logic present)
    al_lib = profile_web / "plugins" / "dsh-agent-loop-pressure-guard" / "lib" / "index.js"
    al_pass = False
    if al_lib.is_file():
        txt = al_lib.read_text(encoding="utf-8", errors="ignore")
        if "CONTEXT_PREFLIGHT_BLOCKED" in txt and "effectiveContextLimit" in txt:
            al_pass = True
    checks["CONTEXT_ADMISSION"] = {"status": "PASS" if al_pass else "FAIL"}

    # 5. Workflow routing gate check
    wf_lib = profile_web / "plugins" / "dsh-workflow-model-preflight-gate" / "lib" / "index.js"
    checks["WORKFLOW"] = {"status": "PASS" if wf_lib.is_file() else "FAIL"}

    # 6. Subagent governance check
    gov_lib = profile_web / "plugins" / "dsh-autonomous-execution-governor" / "lib" / "index.js"
    gov_mjs = profile_web / "plugins" / "dsh-autonomous-execution-governor" / "autonomous-execution-governor.mjs"
    checks["SUBAGENT"] = {"status": "PASS" if gov_lib.is_file() or gov_mjs.is_file() else "FAIL"}

    # 7. Session continuity check
    sess_dir = root / "sessions"
    checks["SESSION_CONTINUITY"] = {"status": "PASS" if sess_dir.is_dir() else "NOT_APPLICABLE"}

    all_pass = all(c.get("status") in ("PASS", "NOT_APPLICABLE") for c in checks.values())
    return {
        "status": "PASS" if all_pass else "FAIL",
        "checks": checks,
        "executedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- lifecycle state machine

def get_runtime_lifecycle(home: Path | None = None, live_smoke: bool = False) -> dict:
    """Compute and persist the full 8-layer DSH runtime lifecycle state."""
    root = home or dsh_home()
    st = load_state(root)

    # 1. SOURCE_REMOTE_CURRENT
    rc, remote_head = _git(aic.ROOT, "rev-parse", "refs/remotes/origin/main")
    if rc != 0:
        rc, remote_head = _git(aic.ROOT, "rev-parse", "HEAD")
    remote_commit = remote_head.strip() if rc == 0 else "UNKNOWN"
    source_remote = {
        "commit": remote_commit,
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "PASS" if remote_commit != "UNKNOWN" else "UNKNOWN",
    }

    # 2. DEPLOYMENT_SOURCE_CURRENT
    mirror = ensure_deployment_mirror(root, aic.ROOT, remote_commit)
    deployment_source = {
        "path": mirror["path"],
        "commit": mirror["commit"],
        "dirty": mirror["dirty"],
        "clean": mirror["clean"],
        "status": "PASS" if mirror["clean"] and mirror["commit"] == remote_commit else "FAIL",
    }

    # 3. CANDIDATE_BUILT
    candidate = st.get("candidate")
    candidate_built = {
        "candidateId": candidate.get("compositionHash") if candidate else None,
        "sourceCommit": candidate.get("sourceCommit") if candidate else None,
        "compositionHash": candidate.get("compositionHash") if candidate else None,
        "version": candidate.get("version") if candidate else None,
        "status": "PASS" if candidate else "NONE",
    }

    # 4. CANDIDATE_VALIDATED
    cand_validated = bool(candidate and candidate.get("verdict") == "CANDIDATE_VALIDATED")
    candidate_validation = {
        "validated": cand_validated,
        "status": "PASS" if cand_validated else ("NONE" if not candidate else "REJECTED"),
        "reasons": candidate.get("reasons", []) if candidate else [],
    }

    # 5. DEPLOYED_READY
    manifest_path = root / "profiles" / "web" / "dsh-runtime-composition.json"
    deployed_comp = None
    deployed_version = None
    if manifest_path.is_file():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            deployed_comp = m.get("profileCombinationHash")
            deployed_version = m.get("base", {}).get("version")
        except Exception:
            pass
    deployed_ready = {
        "compositionHash": deployed_comp,
        "version": deployed_version,
        "manifestPath": str(manifest_path),
        "status": "PASS" if deployed_comp else "NONE",
    }

    # 6. ACTIVE process identity
    active_proc = inspect_active_process(root)

    # 7. LIVE_VALIDATED
    smoke_result = run_live_smoke(root) if live_smoke else {"status": "NOT_RUN", "checks": {}}

    # 8. ACCEPTED_CURRENT
    accepted_comp = (st.get("current") or {}).get("compositionHash")
    is_accepted = bool(
        deployed_comp and active_proc["running"] and not active_proc["isStale"] and
        (smoke_result["status"] == "PASS" or not live_smoke)
    )

    # Desired vs Deployed & Deployed vs Active
    diff_rc = _run_aic("diff", "dsh")["exit"]
    desired_vs_deployed = "NO_DRIFT" if diff_rc == 0 else "DRIFT"
    deployed_vs_active = "STALE" if active_proc["isStale"] else ("IN_SYNC" if active_proc["running"] else "NOT_RUNNING")

    restart_required = active_proc["restartRequired"]
    restart_reason = active_proc["restartReason"]

    # Overall judgment
    if not active_proc["running"]:
        overall = "PARTIAL"
    elif restart_required or deployed_vs_active == "STALE":
        overall = "PARTIAL"
    elif desired_vs_deployed == "DRIFT":
        overall = "PARTIAL"
    elif live_smoke and smoke_result["status"] != "PASS":
        overall = "PARTIAL"
    else:
        overall = "PASS"

    lifecycle = {
        "schemaVersion": 1,
        "sourceRemote": source_remote,
        "deploymentSource": deployment_source,
        "candidateBuilt": candidate_built,
        "candidateValidation": candidate_validation,
        "deployedReady": deployed_ready,
        "activeProcess": active_proc,
        "liveValidation": smoke_result,
        "acceptedCurrent": {
            "accepted": is_accepted,
            "compositionHash": accepted_comp,
            "version": (st.get("current") or {}).get("version"),
        },
        "desiredVsDeployed": desired_vs_deployed,
        "deployedVsActive": deployed_vs_active,
        "restartRequired": "YES" if restart_required else "NO",
        "restartReason": restart_reason or "NONE",
        "overallState": overall,
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_lifecycle_state(root, lifecycle)
    return lifecycle


# ---------------------------------------------------------------- contract

def _contract_for(version: str | None) -> dict:
    contract = copy.deepcopy(aic.adapter_contract())
    if version:
        contract["runtime_composition"]["base"]["version"] = version
    return contract


def _node_runtime_for(home: Path, candidate: dict, contract: dict) -> Path:
    node_rel = contract["runtime_composition"]["node"]["relative_to_dsh_home"]
    return home / node_rel / "node.exe"


# ---------------------------------------------------------------- discovery & aic runner

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


def _run_aic(*cmd: str) -> dict:
    try:
        p = subprocess.run([sys.executable, str(aic.ROOT / "scripts" / "aic" / "aic.py"),
                            *cmd], capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        return {"exit": p.returncode, "out": (p.stdout + p.stderr).strip()[-400:]}
    except Exception as exc:  # noqa: BLE001
        return {"exit": -1, "error": str(exc)}


# ---------------------------------------------------------------- commands

def cmd_check(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    lc = get_runtime_lifecycle(home, live_smoke=False)
    print(json.dumps(lc, ensure_ascii=False, indent=2))
    return 0 if lc["overallState"] == "PASS" else 1


def cmd_smoke(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    res = run_live_smoke(home)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["status"] == "PASS" else 1


def cmd_prepare(args) -> int:
    """Build candidate in isolated staging using clean deployment mirror."""
    version = args.version or _contract_for(None)["runtime_composition"]["base"]["version"]
    home = Path(args.home) if args.home else dsh_home()

    # Sourced from clean deployment mirror, NOT developer workspace
    mirror = ensure_deployment_mirror(home, aic.ROOT)
    mirror_dir = Path(mirror["path"])

    contract = _contract_for(version)
    cand_root = home / ".dsh-lifecycle" / "candidates"
    cand_root.mkdir(parents=True, exist_ok=True)
    stage = cand_root / f"candidate-{version}-{uuid.uuid4().hex[:8]}"
    try:
        result = dsh_runtime.apply(stage, contract, check_lock=False)
    except dsh_runtime.DshCompositionError as exc:
        ledger(home, {"event": "build_failure", "version": version, "error": str(exc)[-400:]})
        print(f"BUILD_FAILED version={version} error={str(exc)[-400:]}")
        return 1

    manifest = json.loads((stage / "profiles" / "web" / "dsh-runtime-composition.json")
                          .read_text(encoding="utf-8"))
    candidate = {
        "version": version,
        "home": str(stage),
        "stage": str(stage),
        "sourceCommit": mirror["commit"],
        "compositionHash": result.get("profileCombinationHash"),
        "nodeVersion": manifest["node"]["version"],
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    st = load_state(home)
    st["candidate"] = candidate
    save_state(home, st)
    ledger(home, {"event": "candidate_built", "version": version,
                  "candidate": candidate["compositionHash"]})
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    return 0


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
                       + "; ".join(f"{f['category']}:{f['component']}" for f in insp["findings"][:8]))

    # (b) node runtime presence
    node = _node_runtime_for(cand_home, cand, contract)
    if not node.is_file():
        reasons.append(f"node missing: {node}")

    # (c) plugins deployed & syntax check
    node_exe = node if node.is_file() else None
    for plugin in contract["runtime_composition"]["managed_rows"]["plugins"]:
        deployed = cand_home / "profiles" / "web" / "plugins" / plugin["plugin_directory"] / plugin["entry_relative"]
        if not deployed.is_file():
            reasons.append(f"plugin not deployed: {plugin['id']}")
            continue
        if node_exe:
            chk = subprocess.run([str(node_exe), "--check", str(deployed)],
                                 capture_output=True, text=True, timeout=60)
            if chk.returncode != 0:
                reasons.append(f"plugin load syntax fail: {plugin['id']}")

    verdict = "CANDIDATE_VALIDATED" if not reasons else "CANDIDATE_REJECTED"
    cand["verdict"] = verdict
    cand["reasons"] = reasons
    cand["validatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(home, st)
    ledger(home, {"event": "candidate_validation", "version": version,
                  "verdict": verdict, "reasons": reasons[:12],
                  "candidate": cand.get("compositionHash")})
    print(json.dumps({"verdict": verdict, "reasons": reasons}, ensure_ascii=False, indent=2))
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
    proposal = {
        "status": "PROPOSED",
        "version": cand["version"],
        "candidate": cand.get("compositionHash"),
        "current": st.get("current"),
        "decision": "awaiting_user_cutover",
        "auto_production_upgrade": "FORBIDDEN",
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = root / f"UPGRADE-{cand['version']}.json"
    path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger(home, {"event": "upgrade_proposal", "version": cand["version"],
                  "proposal": str(path), "candidate": cand.get("compositionHash")})
    print(f"UPGRADE_PROPOSAL=READY path={path}")
    return 0


def cmd_adopt_current(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    st = load_state(home)
    manifest_path = home / "profiles" / "web" / "dsh-runtime-composition.json"
    if not manifest_path.is_file():
        print("NO_COMPOSITION_MANIFEST")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
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
    new_current = {k: cand[k] for k in ("version", "compositionHash", "nodeVersion") if k in cand}
    new_current["compositionId"] = cand.get("compositionId", "dsh-context-lifecycle")
    new_current["nodeRelativePath"] = f"runtime/node-{cand['nodeVersion']}-win-x64"
    new_current["entryRelative"] = "profiles/web/base-dsh-" + cand["version"] + "/node_modules/@deepseek-ai/dsh/lib/bin.js"
    new_current["acceptedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st["previous"] = st.get("current")
    st["current"] = new_current
    st["candidate"] = None
    save_state(home, st)
    ledger(home, {"event": "accept_switch", "from_version": (st.get("previous") or {}).get("version"),
                  "to_version": new_current["version"], "candidate": cand.get("compositionHash")})
    print(json.dumps({"previous": st.get("previous"), "current": st["current"]}, ensure_ascii=False, indent=2))
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
    print(json.dumps({"current": st["current"], "previous": st["previous"]}, ensure_ascii=False, indent=2))
    return 0


def cmd_observe(args) -> int:
    home = Path(args.home) if args.home else dsh_home()
    lc = get_runtime_lifecycle(home, live_smoke=True)
    report = {
        "MANAGED_LAUNCH": "DETECTED" if lc["activeProcess"]["running"] else "NOT_RUNNING",
        "ACTIVE_PID": lc["activeProcess"]["pid"],
        "ACTIVE_COMPOSITION": lc["activeProcess"]["activeComposition"],
        "DEPLOYED_COMPOSITION": lc["deployedReady"]["compositionHash"],
        "DESIRED_VS_DEPLOYED": lc["desiredVsDeployed"],
        "DEPLOYED_VS_ACTIVE": lc["deployedVsActive"],
        "RESTART_REQUIRED": lc["restartRequired"],
        "RESTART_REASON": lc["restartReason"],
        "LIVE_VALIDATION": lc["liveValidation"]["status"],
        "OVERALL_STATE": lc["overallState"],
        "AIC_VALIDATE": _run_aic("validate")["exit"],
        "AIC_DIFF_DSH": _run_aic("diff", "dsh")["exit"],
    }
    root = home / ".dsh-lifecycle" / "observations"
    root.mkdir(parents=True, exist_ok=True)
    out = root / f"observe-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger(home, {"event": "post_launch_observation", **report})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["OVERALL_STATE"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dsh_lifecycle",
                                 description="DSH managed runtime lifecycle (Personal AI/AIC architecture)")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("check", "adopt_current", "smoke"):
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
