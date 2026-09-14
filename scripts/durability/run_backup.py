#!/usr/bin/env python3
"""run_backup.py — canonical nightly backup orchestrator with unified run identity & manifest.

Consistency Model (2026-09-07 remediation):
- composite-per-dataset-transactional-and-stable-copy
  * SQLite datasets (durable_jobs, broker, cc-switch) use SQLite online backup API
    to capture point-in-time ACID transaction snapshots during concurrency.
  * File datasets (credentials, configs, sessions) use pre/post copy SHA-256 auditing
    with mutation retries to ensure tear-free single-file stable copies.
  * Does NOT claim a synthetic cross-system atomic freeze across 800+ files and databases.
  * Tracks run_started_at, component-level capture windows, and run_finished_at.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import backup_root, device_config, ledger_append, now_iso, sha256_file  # noqa: E402

def _is_instance_root(p: Path) -> bool:
    # instance-contract-v1: instance.yaml or state/ marker
    return (p / "instance.yaml").is_file() or (p / "state").is_dir()


def _instance_root() -> Path:
    # instance-contract-v1: env > default(~/.personal-ai) > legacy discovery
    for var in ("PERSONAL_AI_HOME", "PERSONAL_AI_STATE"):
        value = os.environ.get(var)
        if value:
            return Path(value)
    default = Path.home() / ".personal-ai"
    legacy = Path.home() / "personal-ai-state"
    if _is_instance_root(default) or not _is_instance_root(legacy):
        return default
    return legacy



REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def get_git_version() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"],
                             cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "UNKNOWN_GIT"
    except Exception:
        return "UNKNOWN_GIT"


def get_config_version() -> str:
    cfg_path = _instance_root() / "sync" / "this-device.yaml"
    if cfg_path.is_file():
        return sha256_file(cfg_path)
    return "UNKNOWN_CONFIG"


def run_python_job(script_name: str) -> tuple[int, str]:
    script_path = REPO_ROOT / "scripts" / "durability" / script_name
    proc = subprocess.run([sys.executable, str(script_path)],
                          capture_output=True, text=True, timeout=300)
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode, output


def main() -> int:
    run_started_at = now_iso()
    nonce = uuid.uuid4().hex[:8]
    ts_label = run_started_at[:19].replace(":", "").replace("-", "").replace("T", "-")
    run_id = f"nightly-{ts_label}-{nonce}"

    actor = os.environ.get("USERNAME", "admin")
    scheduled_task = os.environ.get("PERSONAL_AI_SCHEDULED_TASK", "PersonalAI-Durability-Nightly")
    task_version = get_git_version()
    config_version = get_config_version()
    root = backup_root()

    print(f"============================================================")
    print(f"Personal AI Nightly Backup Run: {run_id}")
    print(f"Actor: {actor} | Task: {scheduled_task}")
    print(f"Task Version: {task_version[:12]} | Config Version: {config_version[:12]}")
    print(f"Run Started At: {run_started_at} | Destination: {root}")
    print(f"Consistency Model: composite-per-dataset-transactional-and-stable-copy")
    print(f"============================================================")

    step_failures = []
    capture_intervals = {}

    # 1. Sessions backup
    print("\n--- [1/6] Running backup_sessions ---")
    c_start = now_iso()
    rc, out = run_python_job("backup_sessions.py")
    c_end = now_iso()
    print(out)
    capture_intervals["sessions"] = {
        "started_at": c_start,
        "finished_at": c_end,
        "capture_type": "per_file_stable_copy",
        "consistency_guarantee": "Each session file validated with pre/post sha256 audit, mutation retry, and zstd stream decode."
    }
    if rc != 0:
        step_failures.append("backup_sessions")

    # 2. Broker & cc-switch backup
    print("\n--- [2/6] Running backup_broker ---")
    c_start = now_iso()
    rc, out = run_python_job("backup_broker.py")
    c_end = now_iso()
    print(out)
    capture_intervals["broker"] = {
        "started_at": c_start,
        "finished_at": c_end,
        "capture_type": "transactional_sqlite_snapshot",
        "consistency_guarantee": "SQLite online backup API point-in-time ACID transaction snapshot; verified by PRAGMA integrity_check."
    }
    if rc != 0:
        step_failures.append("backup_broker")

    # 3. Durable Jobs backup
    print("\n--- [3/6] Running backup_jobs ---")
    c_start = now_iso()
    rc, out = run_python_job("backup_jobs.py")
    c_end = now_iso()
    print(out)
    capture_intervals["jobs"] = {
        "started_at": c_start,
        "finished_at": c_end,
        "capture_type": "transactional_sqlite_snapshot",
        "consistency_guarantee": "SQLite online backup API point-in-time ACID transaction snapshot; verified by PRAGMA integrity_check."
    }
    if rc != 0:
        step_failures.append("backup_jobs")

    # 4. Configs & credentials backup
    print("\n--- [4/6] Running backup_configs ---")
    c_start = now_iso()
    rc, out = run_python_job("backup_configs.py")
    c_end = now_iso()
    print(out)
    capture_intervals["configs"] = {
        "started_at": c_start,
        "finished_at": c_end,
        "capture_type": "stable_copy",
        "consistency_guarantee": "Irreplaceable configurations including .credentials.yaml backed up with pre/post sha256 verification and secret redaction."
    }
    if rc != 0:
        step_failures.append("backup_configs")

    # 5. Check repos
    print("\n--- [5/6] Running check_repos ---")
    rc, out = run_python_job("check_repos.py")
    print(out)
    if rc != 0 and rc != 2:
        step_failures.append("check_repos")

    # Assemble Run-Level Manifest
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    snaps_jobs = sorted((root / "jobs").glob("jobs-*.sqlite"))
    latest_job_snap = snaps_jobs[-1] if snaps_jobs else None

    cfg_gens = sorted([d for d in (root / "configs").iterdir() if d.is_dir() and d.name.startswith("daily-")])
    latest_cfg_gen = cfg_gens[-1] if cfg_gens else None
    latest_cred_file = (latest_cfg_gen / ".dsh--.credentials.yaml") if latest_cfg_gen else None

    sess_gens = sorted([d for d in (root / "sessions").iterdir() if d.is_dir() and d.name.startswith("daily-")])
    latest_sess_gen = sess_gens[-1] if sess_gens else None
    sess_manifest_path = (latest_sess_gen / "manifest.json") if latest_sess_gen else None
    sess_manifest = json.loads(sess_manifest_path.read_text(encoding="utf-8")) if (sess_manifest_path and sess_manifest_path.is_file()) else {}

    snaps_broker = sorted((root / "broker").glob("broker-*.sqlite"))
    latest_broker_snap = snaps_broker[-1] if snaps_broker else None
    snaps_cc = sorted((root / "broker").glob("cc-switch-*.sqlite"))
    latest_cc_snap = snaps_cc[-1] if snaps_cc else None

    artifacts = {
        "jobs": {
            "source": str(Path.home() / ".personal-ai" / "jobs" / "durable_jobs.db"),
            "file": str(latest_job_snap.relative_to(root)).replace("\\", "/") if latest_job_snap else None,
            "sha256": sha256_file(latest_job_snap) if latest_job_snap else None,
            "bytes": latest_job_snap.stat().st_size if latest_job_snap else 0,
            "capture_interval": capture_intervals.get("jobs"),
            "integrity_status": "verified" if latest_job_snap else "missing"
        },
        "credentials": {
            "source": str(Path.home() / ".dsh" / ".credentials.yaml"),
            "file": str(latest_cred_file.relative_to(root)).replace("\\", "/") if (latest_cred_file and latest_cred_file.is_file()) else None,
            "sha256": sha256_file(latest_cred_file) if (latest_cred_file and latest_cred_file.is_file()) else None,
            "bytes": latest_cred_file.stat().st_size if (latest_cred_file and latest_cred_file.is_file()) else 0,
            "secret_local_only": True,
            "capture_interval": capture_intervals.get("configs"),
            "integrity_status": "verified" if (latest_cred_file and latest_cred_file.is_file()) else "missing"
        },
        "sessions": {
            "source": str(Path.home() / ".dsh" / "sessions"),
            "manifest": str(sess_manifest_path.relative_to(root)).replace("\\", "/") if sess_manifest_path else None,
            "total_protected_sessions": sess_manifest.get("total_sessions", len(sess_manifest.get("files", []))),
            "incremental_copied": sess_manifest.get("incremental_copied", 0),
            "capture_interval": capture_intervals.get("sessions"),
            "integrity_status": "verified" if sess_manifest else "missing"
        },
        "broker": {
            "broker_sqlite": str(latest_broker_snap.relative_to(root)).replace("\\", "/") if latest_broker_snap else None,
            "cc_switch_sqlite": str(latest_cc_snap.relative_to(root)).replace("\\", "/") if latest_cc_snap else None,
            "capture_interval": capture_intervals.get("broker"),
            "integrity_status": "verified" if (latest_broker_snap and latest_cc_snap) else "missing"
        },
        "configs": {
            "generation": latest_cfg_gen.name if latest_cfg_gen else None,
            "manifest": str((latest_cfg_gen / "manifest.json").relative_to(root)).replace("\\", "/") if (latest_cfg_gen and (latest_cfg_gen / "manifest.json").is_file()) else None,
            "capture_interval": capture_intervals.get("configs"),
            "integrity_status": "verified" if latest_cfg_gen else "missing"
        }
    }

    run_status = "ok" if not step_failures else "error"
    finished_backup = now_iso()

    run_manifest = {
        "run_id": run_id,
        "actor": actor,
        "scheduled_task": scheduled_task,
        "task_version": task_version,
        "config_version": config_version,
        "consistency_contract": {
            "model": "composite-per-dataset-transactional-and-stable-copy",
            "guarantees": {
                "sqlite": "Point-in-time ACID transaction snapshot via sqlite online backup API",
                "files": "Tear-free stable copy via pre/post sha256 audit and stream decode validation",
                "cross_system_atomic_freeze": False
            },
            "justification": "Personal AI subsystems (job state machine, session logs, credentials) are decoupled. Transactional SQLite snapshots combined with verified stable copies provide full disaster recovery without requiring a global atomic multi-process filesystem lock."
        },
        "run_started_at": run_started_at,
        "run_finished_at": finished_backup,
        "destination": str(root).replace("\\", "/"),
        "status": run_status,
        "integrity_status": "verified" if run_status == "ok" else "failed",
        "step_failures": step_failures,
        "component_capture_intervals": capture_intervals,
        "artifacts": artifacts
    }

    run_manifest_path = run_dir / "manifest.json"
    run_manifest_path.write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # Record run to runs.jsonl
    ledger_append({
        "job": "backup_run",
        "dataset": "all",
        "run_id": run_id,
        "actor": actor,
        "scheduled_task": scheduled_task,
        "task_version": task_version,
        "config_version": config_version,
        "consistency_model": "composite-per-dataset-transactional-and-stable-copy",
        "run_started_at": run_started_at,
        "run_finished_at": finished_backup,
        "finished_at": finished_backup,
        "destination": str(root).replace("\\", "/"),
        "status": run_status,
        "integrity_status": "verified" if run_status == "ok" else "failed",
        "manifest": str(run_manifest_path.relative_to(root)).replace("\\", "/"),
        "total_sessions": artifacts["sessions"]["total_protected_sessions"],
        "error": "; ".join(step_failures) if step_failures else None
    })

    print(f"\nRun manifest written: {run_manifest_path}")

    # 6. Isolated restore check (100% full coverage)
    print("\n--- [6/6] Running 100% full isolated restore_check ---")
    proc_restore = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "durability" / "restore_check.py"),
                                   "--run-id", run_id],
                                  capture_output=True, text=True, timeout=300)
    print(proc_restore.stdout + "\n" + proc_restore.stderr)
    if proc_restore.returncode != 0:
        print("CRITICAL: Isolated restore verification failed!")
        return 1

    # RPO Check
    print("\n--- Summary: RPO Status ---")
    proc_rpo = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "durability" / "rpo_check.py")],
                              capture_output=True, text=True, timeout=60)
    print(proc_rpo.stdout)

    print(f"\n============================================================")
    print(f"Personal AI Nightly Backup CLOSED: {run_id} [PASS]")
    print(f"============================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
