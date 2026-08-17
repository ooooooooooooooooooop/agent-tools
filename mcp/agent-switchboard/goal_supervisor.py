"""goal_supervisor.py — broker-owned supervision state for Codex Goal runs (Phase 1 observability + Phase 2 enforcement).

GitHub issue #4: a durable Codex Goal objective is not the same as governed
long-horizon execution. Persistence alone does not prevent drift, repeated
low-value work, local artifacts being treated as overall progress, a partial
blocker becoming a global stop, or unbounded budget consumption without
converging on the user's end state.

This module gives the broker a deterministic, local, broker-owned layer around
an existing Codex Goal run:

  * capability probe          — what the installed Codex surface can READ vs ENFORCE (honest);
  * contract validation       — rejects unbounded objectives/criteria/budgets deterministically;
  * criterion ledger          — broker-owned per-criterion state persisted under ~/.agent-broker/goals/;
  * host-computed completion  — a worker's prose claim of completion is never accepted as proof.

Phase 2 (enforcement) uses the reserved ledger fields:

  * work-unit dispatch        — `dispatch_criterion` picks the next pending/inconclusive criterion;
  * verifier execution        — `run_verifier` marks verified ONLY on a successful configured command;
  * action fingerprinting     — `fingerprint_evidence` detects repeated low-value evidence;
  * budget enforcement        — `enforce_budgets` fails closed on max_actions/token/time breaches.

Constraints honoured (issue #4):
  * no second open-ended "manager agent" — every function here is deterministic code;
  * no duplicate Codex Goal state as a competing source of truth — Codex's own
    ``~/.codex/goals_1.sqlite`` is read read-only, never written by this module;
  * no periodic model call asking "is progress happening" — idle consumes zero supervision tokens;
  * no enforcement claim when the installed surface only exposes observation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from atomic_io import FileLock, atomic_write_text


# --- locations -------------------------------------------------------------
BROKER_DIR = Path(os.environ.get("AGENT_BROKER_HOME", Path.home() / ".agent-broker"))
GOALS_ROOT = BROKER_DIR / "goals"
CODEX_GOALS_DB = Path.home() / ".codex" / "goals_1.sqlite"
CODEX_HOME = Path.home() / ".codex"

GOAL_REF_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
CRITERION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

# Criterion status vocabulary. Phase 1 never sets "verified" — only a configured
# verifier may do that (Phase 2). A worker's self-report is never sufficient.
CRITERION_STATUSES = ("pending", "running", "inconclusive", "blocked", "verified")

GOAL_STATUSES = ("in_progress", "attention_required", "blocked", "completed")

# Deterministic open-ended markers used to reject unbounded objectives. A goal
# contract must be bounded to be supervised; "find the best X until a winner
# exists" is exactly the shape the issue asks us to reject.
UNBOUNDED_PATTERNS: tuple[str, ...] = (
    r"\bbest\b",
    r"\boptimal\b",
    r"\buntil\b",
    r"\bwhenever\b",
    r"\bas good as possible\b",
    r"\bmaximi[sz]e\b",
    r"\bminimi[sz]e\b",
    r"\ball possible\b",
    r"\ball available\b",
    r"\bnever stop\b",
    r"\bforever\b",
    r"\bindefinitely\b",
    r"\bwin(?:ner|ning)?\b",
    "最优",
    "最佳",
    "最大化",
    "最小化",
    "永不",
    "永远",
    "越来越好",
    "无上限",
    "越强越好",
)

BUDGET_KEYS = ("total_tokens", "total_seconds", "max_actions")

# Phase 2 constants. A criterion is only "verified" by running its configured
# verifier (never a worker's prose). If it fails that many times it is blocked.
DEFAULT_MAX_VERIFIER_ATTEMPTS = 3
# A command that does not finish within this many seconds fails closed (timeout).
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def compact_text(value: Any, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 16)].rstrip() + " ... [truncated]"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"goal_supervisor_state_invalid: {path}") from exc


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_goal_ref(value: Any) -> str:
    goal_ref = str(value or "").strip()
    if not GOAL_REF_RE.fullmatch(goal_ref):
        raise ValueError("goal_ref must contain 1-100 letters, digits, dot, underscore, colon, or hyphen")
    return goal_ref


def validate_criterion_id(value: Any) -> str:
    criterion_id = str(value or "").strip()
    if not CRITERION_ID_RE.fullmatch(criterion_id):
        raise ValueError("criterion_id must contain 1-64 letters, digits, dot, underscore, colon, or hyphen")
    return criterion_id


def goal_dir(broker_home: Path, goal_ref: str) -> Path:
    return Path(broker_home) / "goals" / validate_goal_ref(goal_ref)


def objective_hash(objective: str) -> str:
    return hashlib.sha256(str(objective or "").encode("utf-8")).hexdigest()


# --- 1. capability probe ---------------------------------------------------

def _find_codex_cli() -> str | None:
    """Locate the codex CLI deterministically. Order: explicit path, the
    CODEX_CLI_PATH marker from ~/.codex/config.toml, PATH, then a
    %LOCALAPPDATA%/OpenAI/Codex/bin/*/codex.exe glob (the install location on
    this machine when codex is not on PATH)."""
    candidates: list[str] = []
    for token in os.environ.get("CODEX_CLI_PATH", "").split(os.pathsep):
        if token:
            candidates.append(token)
    try:
        text = (CODEX_HOME / "config.toml").read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"CODEX_CLI_PATH\s*=\s*[\"']?([^\"'\n\r]+)", text):
            candidates.append(match.group(1).strip())
    except OSError:
        pass
    found = shutil.which("codex") or shutil.which("codex.exe")
    if found:
        candidates.append(found)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.extend(
            str(path) for path in sorted(Path(local_appdata).glob("OpenAI/Codex/bin/*/codex.exe"))
        )
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return None


def _smoke_version(codex_path: str) -> tuple[bool, str | None]:
    """``codex --version`` smoke test. No model call."""
    try:
        result = subprocess.run(
            [codex_path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None
    line = (result.stdout or result.stderr or "").strip().splitlines()
    return result.returncode == 0, (line[0][:80] if line else None)


def _probe_dispatch(codex_path: str) -> bool:
    """Deterministically check the codex CLI can dispatch a bounded continuation
    (``codex exec --resume <session>``). Reads help text only — no model call."""
    try:
        result = subprocess.run(
            [codex_path, "exec", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    text = f"{result.stdout}\n{result.stderr}"
    return bool(re.search(r"^.*resume.*$", text, re.MULTILINE)) and "resume" in text.lower()


def _probe_goals_db() -> dict[str, Any]:
    """Read-only probe of ~/.codex/goals_1.sqlite: is the goal state table
    present, and are the usage-telemetry columns readable?"""
    result: dict[str, Any] = {
        "goal_state_readable": False,
        "goal_usage_readable": False,
        "detail": {"db_path": str(CODEX_GOALS_DB)},
    }
    db = CODEX_GOALS_DB
    if not db.exists():
        result["detail"]["error"] = "no_goals_db"
        return result
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.5)
        try:
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.Error as exc:
            result["detail"]["error"] = f"goals_db_unreadable: {exc}"
            return result
        if "thread_goals" not in tables:
            result["detail"]["error"] = "no_thread_goals_table"
            return result
        columns = {
            row[1]
            for row in con.execute("PRAGMA table_info(thread_goals)")
        }
        result["goal_state_readable"] = True
        result["detail"]["thread_goals_columns"] = sorted(columns)
        usage_columns = {"token_budget", "tokens_used", "time_used_seconds"}
        result["goal_usage_readable"] = usage_columns.issubset(columns)
        try:
            count = con.execute("SELECT COUNT(*) FROM thread_goals").fetchone()[0]
            result["detail"]["active_goal_rows"] = int(count)
        except sqlite3.Error:
            result["detail"]["active_goal_rows"] = None
    except sqlite3.Error as exc:
        result["detail"]["error"] = f"goals_db_unreadable: {exc}"
    finally:
        try:
            con.close()  # type: ignore[possibly-undefined]
        except (NameError, sqlite3.Error):
            pass
    return result


def probe_goal_capabilities(codex_path: str | None = None) -> dict[str, Any]:
    """Deterministic, local, read-only capability report for supervising a Codex
    Goal run. Never mutates state and never calls a model.

    Honesty contract (issue #4 behaviour 1): if the installed Codex surface can
    only be observed, ``observability_only`` is true and enforcement is not
    claimed. ``not_available`` when goal state cannot even be read."""
    codex = codex_path or _find_codex_cli()
    db = _probe_goals_db()
    goal_state_readable = bool(db["goal_state_readable"])
    goal_usage_readable = bool(db["goal_usage_readable"])

    dispatch_available = False
    version: str | None = None
    smoke_ok = False
    if codex:
        smoke_ok, version = _smoke_version(codex)
        if smoke_ok:
            dispatch_available = _probe_dispatch(codex)

    # broker-owned ledger can always be created; "completion enforceable" means
    # we can read goal state AND can gate future work-unit dispatch.
    completion_enforceable = bool(goal_state_readable and dispatch_available)

    result: dict[str, Any] = {
        "goal_state_readable": goal_state_readable,
        "goal_usage_readable": goal_usage_readable,
        "goal_pause_resume_available": None,  # not exposed by this Codex version — never claim it
        "goal_work_dispatch_available": dispatch_available,
        "goal_completion_enforceable": completion_enforceable,
        "observability_only": bool(goal_state_readable and not dispatch_available),
        "not_available": bool(not goal_state_readable),
        "detail": {
            **db.get("detail", {}),
            "codex_path": codex,
            "codex_smoke_ok": smoke_ok,
            "codex_version": version,
            "dispatch_via": "codex exec --resume" if dispatch_available else None,
        },
    }
    return result


# --- 2. contract validation -------------------------------------------------

def _unbounded_matches(objective: str) -> list[str]:
    matches: list[str] = []
    for pattern in UNBOUNDED_PATTERNS:
        if re.search(pattern, objective, re.IGNORECASE):
            matches.append(pattern)
    return matches


def validate_goal_contract(
    objective: str,
    criteria: list[dict[str, Any]],
    boundaries: list[str] | None = None,
    budgets: dict[str, Any] | None = None,
    unbudgeted: bool = False,
) -> dict[str, Any]:
    """Deterministic Goal contract validation.

    A supervised Goal requires (issue #4 behaviour 2):
      * one immutable objective (retained verbatim + sha256);
      * bounded mandatory completion criteria with required evidence + a verifier
        + a stopping test;
      * explicit protected boundaries;
      * an actual token/time/action budget OR an explicit user-approved
        ``unbudgeted`` mode.

    Rejects ``goal_contract_unbounded`` for unbounded superlatives such as
    "find the best strategy until a winner exists". No model call."""
    result: dict[str, Any] = {
        "valid": True,
        "reason": None,
        "objective_hash": objective_hash(objective),
        "unbounded_matches": [],
        "errors": [],
    }
    objective_text = str(objective or "").strip()
    if not objective_text:
        result["valid"] = False
        result["errors"].append("objective_required")
        return result

    matches = _unbounded_matches(objective_text)
    if matches:
        result["valid"] = False
        result["reason"] = "goal_contract_unbounded"
        result["unbounded_matches"] = matches
        result["errors"].append("objective_unbounded_terms")
        return result

    if not isinstance(criteria, list) or not criteria:
        result["valid"] = False
        result["errors"].append("criteria_required")
        return result

    normalized_criteria: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    raw_by_id: dict[str, dict[str, Any]] = {}
    for raw in criteria:
        if not isinstance(raw, dict):
            result["valid"] = False
            result["errors"].append("criterion_not_object")
            continue
        criterion_id = str(raw.get("id") or "").strip()
        if not criterion_id or not CRITERION_ID_RE.fullmatch(criterion_id):
            result["valid"] = False
            result["errors"].append(f"criterion_invalid_id: {criterion_id or '(empty)'}")
            continue
        if criterion_id in seen_ids:
            result["valid"] = False
            result["errors"].append(f"criterion_duplicate_id: {criterion_id}")
            continue
        seen_ids.add(criterion_id)
        raw_by_id[criterion_id] = raw

    # Two-phase normalization: first pass records every valid criterion, second pass
    # resolves `dependencies` against the full id set (forward refs allowed).
    for criterion_id, raw in raw_by_id.items():
        mandatory = bool(raw.get("mandatory", True))
        required_evidence = [str(item).strip() for item in (raw.get("required_evidence") or [])]
        required_evidence = [item for item in required_evidence if item]
        verifier = str(raw.get("verifier") or "").strip()
        stopping_test = str(raw.get("stopping_test") or "").strip()
        if mandatory:
            if not required_evidence:
                result["valid"] = False
                result["errors"].append(f"criterion_missing_evidence: {criterion_id}")
            if not verifier:
                result["valid"] = False
                result["errors"].append(f"criterion_missing_verifier: {criterion_id}")
            if not stopping_test:
                result["valid"] = False
                result["errors"].append(f"criterion_missing_stopping_test: {criterion_id}")
        dependencies: list[str] = []
        for dep in (raw.get("dependencies") or []):
            dep = str(dep).strip()
            if not dep:
                continue
            if dep not in seen_ids:
                result["valid"] = False
                result["errors"].append(f"criterion_unknown_dependency: {criterion_id}->{dep}")
                continue
            if dep != criterion_id and dep not in dependencies:
                dependencies.append(dep)
        criterion_budget: dict[str, Any] = {}
        raw_budget = raw.get("budget")
        if isinstance(raw_budget, dict):
            for key in BUDGET_KEYS:
                value = raw_budget.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    criterion_budget[key] = int(value)
        normalized_criteria.append(
            {
                "id": criterion_id,
                "description": str(raw.get("description") or "").strip(),
                "mandatory": mandatory,
                "required_evidence": required_evidence,
                "verifier": verifier,
                "stopping_test": stopping_test,
                "dependencies": dependencies,
                "budget": criterion_budget,
                "alternative_routes": [
                    str(route).strip()
                    for route in (raw.get("alternative_routes") or [])
                    if str(route).strip()
                ],
            }
        )

    normalized_boundaries = [str(item).strip() for item in (boundaries or []) if str(item).strip()]

    budget_plan: dict[str, Any] = {}
    if isinstance(budgets, dict):
        for key in BUDGET_KEYS:
            value = budgets.get(key)
            if isinstance(value, (int, float)) and value > 0:
                budget_plan[key] = int(value)
    if not budget_plan and not unbudgeted:
        result["valid"] = False
        result["reason"] = "goal_contract_unbounded"
        result["errors"].append("budget_required_or_unbudgeted")

    result["objective"] = objective_text
    result["criteria"] = normalized_criteria
    result["boundaries"] = normalized_boundaries
    result["budget_plan"] = budget_plan
    result["unbudgeted"] = bool(unbudgeted)
    if not result["valid"]:
        result["reason"] = result["reason"] or "goal_contract_invalid"
    return result


# --- 3. broker-owned criterion ledger ---------------------------------------

def _ledger_seed(config: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": 1,
        "goal_ref": config["goal_ref"],
        "objective_hash": config["objective_hash"],
        "status": "in_progress",
        "budget_plan": config.get("budget_plan") or {},
        "unbudgeted": bool(config.get("unbudgeted")),
        "criteria": {
            criterion["id"]: {
                "status": "pending",
                "mandatory": bool(criterion.get("mandatory", True)),
                "dependencies": list(criterion.get("dependencies") or []),
                "required_evidence": list(criterion.get("required_evidence") or []),
                "observed_evidence": [],
                "verifier": criterion.get("verifier") or "",
                "route": None,
                "attempts": 0,
                "last_fingerprint": None,
                "usage": {},
                "budget": dict(criterion.get("budget") or {}),
                "updated_at": now,
            }
            for criterion in (config.get("criteria") or [])
        },
        "usage": {},
        "created_at": now,
        "updated_at": now,
    }


def _append_action(goal_ref: str, event_type: str, **fields: Any) -> dict[str, Any]:
    path = goal_dir(BROKER_DIR, goal_ref) / "actions.jsonl"
    recent = _recent_jsonl(path, 1)
    seq = int(recent[-1].get("seq") or 0) if recent else 0
    payload = {"seq": seq + 1, "type": event_type, "created_at": utc_now(), **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def _recent_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as exc:
        raise RuntimeError(f"goal_supervisor_state_unavailable: {path}") from exc
    result: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _update_ledger(goal_ref: str, update) -> dict[str, Any] | None:
    """Serialized read/modify/write of the criterion ledger under a short lock.
    Timeout is fail-open so CLI verbs never wedge."""
    directory = goal_dir(BROKER_DIR, goal_ref)
    try:
        with FileLock(directory / "ledger.lock", timeout=1.0, stale_seconds=30.0):
            state = _read_json(directory / "ledger.json", None)
            if not isinstance(state, dict):
                return None
            update(state)
            state["updated_at"] = utc_now()
            _write_json(directory / "ledger.json", state)
            return state
    except (OSError, TimeoutError, RuntimeError):
        return None


def create_goal(
    objective: str,
    criteria: list[dict[str, Any]],
    boundaries: list[str] | None = None,
    budgets: dict[str, Any] | None = None,
    unbudgeted: bool = False,
    codex_thread_id: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Validate a Goal contract and, if bounded, create broker-owned ledger state.

    Returns the contract result (``valid: False`` is an honest rejection, e.g.
    ``goal_contract_unbounded``) or the created goal summary. No model call."""
    validated = validate_goal_contract(objective, criteria, boundaries, budgets, unbudgeted)
    if not validated["valid"]:
        return {
            "created": False,
            "valid": False,
            "reason": validated["reason"],
            "unbounded_matches": validated.get("unbounded_matches") or [],
            "errors": validated["errors"],
        }

    goal_ref = f"goal-{uuid.uuid4().hex[:12]}"
    now = utc_now()
    config: dict[str, Any] = {
        "schema_version": 1,
        "goal_ref": goal_ref,
        "objective": validated["objective"],
        "objective_hash": validated["objective_hash"],
        "criteria": validated["criteria"],
        "boundaries": validated["boundaries"],
        "budget_plan": validated["budget_plan"],
        "unbudgeted": validated["unbudgeted"],
        "codex_thread_id": codex_thread_id or None,
        "created_by": created_by or None,
        "created_at": now,
    }
    directory = goal_dir(BROKER_DIR, goal_ref)
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / "config.json", config)
    _write_json(directory / "ledger.json", _ledger_seed(config))
    _append_action(goal_ref, "contract_accepted", objective_hash=config["objective_hash"])

    return {
        "created": True,
        "goal_ref": goal_ref,
        "status": "in_progress",
        "objective_hash": config["objective_hash"],
        "unbudgeted": config["unbudgeted"],
    }


def list_goals() -> dict[str, Any]:
    """List broker-owned goal summaries. Read-only."""
    goals: list[dict[str, Any]] = []
    root = GOALS_ROOT
    if root.exists():
        for config_path in sorted(root.glob("*/config.json")):
            try:
                config = _read_json(config_path, None)
                ledger = _read_json(config_path.parent / "ledger.json", None)
            except RuntimeError:
                continue
            if not isinstance(config, dict):
                continue
            criteria = config.get("criteria") or []
            if isinstance(ledger, dict):
                verified = sum(
                    1
                    for item in ledger.get("criteria", {}).values()
                    if isinstance(item, dict) and item.get("status") == "verified"
                )
            else:
                verified = 0
            goals.append(
                {
                    "goal_ref": config.get("goal_ref"),
                    "status": ledger.get("status") if isinstance(ledger, dict) else None,
                    "objective": compact_text(config.get("objective"), 120),
                    "objective_hash": config.get("objective_hash"),
                    "criteria_count": len(criteria),
                    "criteria_verified": verified,
                    "unbudgeted": bool(config.get("unbudgeted")),
                    "codex_thread_id": config.get("codex_thread_id"),
                }
            )
    return {"goals": goals}


# --- 4. host-computed status / completion -----------------------------------

def _read_thread_usage(codex_thread_id: str | None) -> dict[str, Any] | None:
    """Read live Goal usage telemetry from Codex's own goal DB (read-only)."""
    if not codex_thread_id:
        return None
    db = CODEX_GOALS_DB
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.5)
        try:
            row = con.execute(
                "SELECT status, token_budget, tokens_used, time_used_seconds, "
                "updated_at_ms FROM thread_goals WHERE thread_id = ?",
                (codex_thread_id,),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return {
        "goal_status": row[0],
        "token_budget": row[1],
        "tokens_used": row[2],
        "time_used_seconds": row[3],
        "updated_at_ms": row[4],
    }


def _evidence_resolvable(evidence_refs: list[str]) -> tuple[bool, list[str]]:
    """A light Phase-1 resolvability check: every declared evidence ref must be a
    non-empty string; a ref that looks like an absolute filesystem path must
    exist on disk. Non-path refs (test names, output keys) are declared refs."""
    missing: list[str] = []
    for ref in evidence_refs or []:
        ref = str(ref).strip()
        if not ref:
            missing.append("(empty)")
            continue
        path = Path(ref)
        if path.is_absolute() and not path.exists():
            missing.append(ref)
    return (not missing), missing


def compute_completion(config: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    """Host-computed completion (issue #4 behaviour 8). A worker's prose claim is
    never accepted; completion requires every mandatory criterion verified,
    evidence resolvable, objective hash unchanged, boundaries intact, and no
    unresolved mandatory blocker."""
    missing: list[str] = []

    if config.get("objective_hash") != ledger.get("objective_hash"):
        return {
            "completed": False,
            "reason": "objective_hash_changed",
            "missing": ["objective hash mismatch"],
        }

    blocked: list[str] = []
    for criterion in (config.get("criteria") or []):
        if not criterion.get("mandatory"):
            continue
        criterion_id = criterion["id"]
        item = (ledger.get("criteria") or {}).get(criterion_id)
        item = item if isinstance(item, dict) else {}
        if item.get("status") == "blocked":
            blocked.append(criterion_id)
        if item.get("status") != "verified":
            missing.append(f"{criterion_id}: not verified")
        ok, unresolvable = _evidence_resolvable(item.get("observed_evidence") or [])
        if not ok:
            missing.append(f"{criterion_id}: unresolved evidence {unresolvable}")

    if missing or blocked:
        return {
            "completed": False,
            "reason": "blocker_open" if blocked else "criteria_unverified",
            "missing": missing or [f"{criterion_id}: blocked" for criterion_id in blocked],
        }
    return {"completed": True, "reason": None, "missing": []}


def get_goal_status(goal_ref: str, recent_actions: int = 10) -> dict[str, Any]:
    """Broker-owned goal status: ledger state + live Codex usage (when readable)
    + host-computed completion. Read-only except for nothing — no writes here."""
    goal_ref = validate_goal_ref(goal_ref)
    directory = goal_dir(BROKER_DIR, goal_ref)
    config = _read_json(directory / "config.json", None)
    ledger = _read_json(directory / "ledger.json", None)
    if not isinstance(config, dict) or not isinstance(ledger, dict):
        raise ValueError(f"goal_supervisor_unknown_goal: {goal_ref}")

    completion = compute_completion(config, ledger)
    usage = _read_thread_usage(config.get("codex_thread_id"))
    if usage is not None:
        ledger_usage = dict(ledger.get("usage") or {})
        ledger_usage["codex_live"] = usage
        ledger["usage"] = ledger_usage

    criteria_status: dict[str, Any] = {}
    for criterion in (config.get("criteria") or []):
        item = (ledger.get("criteria") or {}).get(criterion["id"])
        item = item if isinstance(item, dict) else {}
        criteria_status[criterion["id"]] = {
            "status": item.get("status", "pending"),
            "mandatory": bool(criterion.get("mandatory")),
            "required_evidence": list(criterion.get("required_evidence") or []),
            "observed_evidence": list(item.get("observed_evidence") or []),
            "verifier": item.get("verifier") or "",
            "attempts": int(item.get("attempts") or 0),
        }

    return {
        "goal_ref": goal_ref,
        "objective": config.get("objective"),
        "objective_hash": config.get("objective_hash"),
        "hash_match": config.get("objective_hash") == ledger.get("objective_hash"),
        "status": ledger.get("status"),
        "budget_plan": ledger.get("budget_plan") or {},
        "unbudgeted": bool(ledger.get("unbudgeted")),
        "usage": ledger.get("usage") or {},
        "completion": completion,
        "criteria": criteria_status,
        "boundaries": list(config.get("boundaries") or []),
        "recent_actions": _recent_jsonl(directory / "actions.jsonl", max(0, min(int(recent_actions), 50))),
        "updated_at": ledger.get("updated_at"),
    }


# --- 5. evidence recording (operator-driven, no auto-verify) -----------------

def record_evidence(
    goal_ref: str,
    criterion_id: str,
    evidence_refs: list[str],
    status_hint: str | None = None,
) -> dict[str, Any]:
    """Append observed evidence to a criterion and, optionally, move it to
    ``inconclusive`` or ``blocked`` (operator judgement). Phase 1 never sets
    ``verified``: only a configured verifier may do that (Phase 2), so a worker
    can never complete a Goal on its own prose."""
    goal_ref = validate_goal_ref(goal_ref)
    criterion_id = validate_criterion_id(criterion_id)
    refs = [str(item).strip() for item in (evidence_refs or []) if str(item).strip()]
    if status_hint is not None and status_hint not in ("pending", "running", "inconclusive", "blocked"):
        return {"recorded": False, "error": f"status_not_allowed: {status_hint}"}

    state = _update_ledger(
        goal_ref,
        lambda s: _apply_evidence(s, criterion_id, refs, status_hint),
    )
    if state is None:
        return {"recorded": False, "error": f"goal_supervisor_unknown_goal: {goal_ref}"}

    item = (state.get("criteria") or {}).get(criterion_id)
    _append_action(
        goal_ref,
        "evidence_recorded",
        criterion_id=criterion_id,
        status=item.get("status") if isinstance(item, dict) else None,
        evidence_refs=refs,
        status_hint=status_hint,
    )
    return {
        "recorded": True,
        "goal_ref": goal_ref,
        "criterion_id": criterion_id,
        "status": item.get("status") if isinstance(item, dict) else None,
        "observed_evidence": list(item.get("observed_evidence") or []) if isinstance(item, dict) else [],
    }


def _apply_evidence(state: dict[str, Any], criterion_id: str, refs: list[str], status_hint: str | None) -> None:
    criteria = state.get("criteria")
    if not isinstance(criteria, dict) or criterion_id not in criteria:
        raise ValueError(f"goal_supervisor_unknown_criterion: {criterion_id}")
    item = criteria[criterion_id]
    if not isinstance(item, dict):
        item = {}
        criteria[criterion_id] = item
    observed = list(item.get("observed_evidence") or [])
    for ref in refs:
        if ref not in observed:
            observed.append(ref)
    item["observed_evidence"] = observed
    if status_hint is not None:
        item["status"] = status_hint
    item["updated_at"] = utc_now()
    _recompute_status(state)


def _dependency_chain_blocked(state: dict[str, Any], criterion_id: str, visited: set[str] | None = None) -> bool:
    """True when criterion_id itself is blocked AND every dependency in its chain is
    also blocked (issue #4 acceptance 4: a blocker only becomes a global stop when the
    dependency graph proves the chain is fully blocked). A leaf criterion (no
    dependencies) that is blocked returns True — its own block is the terminal proof;
    it stops ITSELF, not unrelated criteria."""
    criteria = state.get("criteria") or {}
    item = criteria.get(criterion_id)
    if not isinstance(item, dict):
        return True
    if item.get("status") != "blocked":
        return False
    deps = [str(d).strip() for d in (item.get("dependencies") or [])]
    if not deps:
        return True  # leaf blocked -> its own chain is blocked (local to this criterion's subtree)
    visited = visited or set()
    if criterion_id in visited:
        return False  # cycle cannot be proven fully blocked
    visited = visited | {criterion_id}
    for dep in deps:
        dep_item = criteria.get(dep)
        if not isinstance(dep_item, dict) or dep_item.get("status") != "blocked":
            return False  # a dependency is not blocked -> chain not fully blocked
        if not _dependency_chain_blocked(state, dep, visited):
            return False
    return True


def _goal_status_from_criteria(state: dict[str, Any]) -> str:
    """Derive the Goal-level status deterministically from criterion states with a
    dependency-aware blocker rule (issue #4 acceptance 4/5):
      * blocked (global) only when EVERY unresolvable mandatory criterion is blocked
        and its dependency chain is fully blocked — i.e. the dependency graph proves
        there is no remaining path to advance;
      * attention_required when at least one mandatory criterion is blocked or
        inconclusive but other ready criteria can still advance;
      * else in_progress."""
    criteria = state.get("criteria") or {}
    mandatory_items = [
        (cid, item)
        for cid, item in criteria.items()
        if isinstance(item, dict) and bool(item.get("mandatory", True))
    ]
    if not mandatory_items:
        return "in_progress"
    any_blocked = False
    any_open = False  # pending/running/inconclusive (advanceable) mandatory
    for cid, item in mandatory_items:
        status = item.get("status")
        if status == "blocked":
            any_blocked = True
        elif status != "verified":
            any_open = True
    if any_open:
        # There is at least one advanceable mandatory criterion -> never a global stop.
        if any_blocked or any(
            item.get("status") == "inconclusive"
            for _, item in mandatory_items
        ):
            return "attention_required"
        return "in_progress"
    # No advanceable mandatory remains: blocked ones must prove a fully-blocked chain,
    # otherwise (a local blocker with nothing else to advance) still attention, never
    # silently complete.
    all_chain_blocked = all(
        _dependency_chain_blocked(state, cid)
        for cid, item in mandatory_items
        if item.get("status") == "blocked"
    )
    if all_chain_blocked and any_blocked:
        return "blocked"
    return "attention_required"


def _recompute_status(state: dict[str, Any]) -> None:
    """Recompute Goal-level status from criterion states (dependency-aware)."""
    state["status"] = _goal_status_from_criteria(state)


def complete_goal(goal_ref: str) -> dict[str, Any]:
    """Host-computed completion check for a goal. Honest: without verified
    criteria (only reachable through a verifier in Phase 2), completion stays
    not-completed — a worker's self-report is never accepted."""
    goal_ref = validate_goal_ref(goal_ref)
    directory = goal_dir(BROKER_DIR, goal_ref)
    config = _read_json(directory / "config.json", None)
    ledger = _read_json(directory / "ledger.json", None)
    if not isinstance(config, dict) or not isinstance(ledger, dict):
        return {"goal_ref": goal_ref, "completed": False, "reason": "goal_unknown", "missing": []}
    completion = compute_completion(config, ledger)
    return {
        "goal_ref": goal_ref,
        "completed": completion["completed"],
        "reason": completion["reason"],
        "missing": completion["missing"],
        "criteria": {
            criterion_id: (item.get("status") if isinstance(item, dict) else None)
            for criterion_id, item in (ledger.get("criteria") or {}).items()
        },
    }


# --- 5b. Phase 2: work-unit dispatch, verifier execution, budget enforcement ---

def _run_command(
    argv: list[str],
    cwd: str | None = None,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    stdin_data: str | None = None,
) -> dict[str, Any]:
    """Deterministic local command execution for a verifier or stopping test. No model
    call. Fails closed (``timed_out``/``spawn_failed``) on any infrastructure failure so
    a criterion is never marked verified on an unresolved command."""
    import shlex

    if not argv:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "empty_command", "timed_out": False}
    try:
        proc = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
            stdin=subprocess.PIPE if stdin_data is not None else None,
            input=stdin_data,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "timeout", "timed_out": True}
    except (OSError, ValueError) as exc:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": f"spawn_failed: {exc}", "timed_out": False}
    return {
        "ok": proc.returncode == 0,
        "exit_code": int(proc.returncode),
        "stdout": (proc.stdout or "")[:4000],
        "stderr": (proc.stderr or "")[:2000],
        "timed_out": False,
    }


def _parse_shell(cmd: str) -> list[str]:
    """Split a shell-ish command into argv deterministically. On Windows prefer
    ``cmd /c <cmd>`` so the verifier/stopping test can use normal shell syntax;
    elsewhere run via the POSIX shell."""
    import shlex

    cmd = str(cmd or "").strip()
    if not cmd:
        return []
    if os.name == "nt":
        return ["cmd", "/c", cmd]
    return ["sh", "-c", cmd]


def run_verifier(goal_ref: str, criterion_id: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
    """Run a criterion's configured verifier (Phase 2). Only a successful verifier exit
    marks the criterion ``verified``; a failing verifier increments ``attempts`` and,
    past the max, moves it to ``blocked``. Never accepts prose as proof."""
    goal_ref = validate_goal_ref(goal_ref)
    criterion_id = validate_criterion_id(criterion_id)
    config = _read_json(goal_dir(BROKER_DIR, goal_ref) / "config.json", None)
    if not isinstance(config, dict):
        return {"verified": False, "reason": "goal_unknown"}
    verifier = ""
    criterion = next((c for c in (config.get("criteria") or []) if c.get("id") == criterion_id), None)
    if criterion:
        verifier = str(criterion.get("verifier") or "").strip()
    if not verifier:
        return {"verified": False, "reason": "no_verifier_configured"}

    result = _run_command(_parse_shell(verifier), timeout_seconds=timeout_seconds or DEFAULT_COMMAND_TIMEOUT_SECONDS)
    outcome = _update_ledger(
        goal_ref,
        lambda s: _apply_verifier_outcome(
            s, criterion_id, ok=result["ok"], exit_code=result.get("exit_code"),
            stderr=result.get("stderr") or "", timeout=bool(result.get("timed_out")),
        ),
    )
    item = (outcome.get("criteria") or {}).get(criterion_id) if isinstance(outcome, dict) else None
    status = item.get("status") if isinstance(item, dict) else None
    _append_action(
        goal_ref,
        "verifier_result",
        criterion_id=criterion_id,
        ok=result["ok"],
        exit_code=result.get("exit_code"),
        status=status,
        attempts=int(item.get("attempts") or 0) if isinstance(item, dict) else 0,
        timed_out=bool(result.get("timed_out")),
    )
    return {
        "goal_ref": goal_ref,
        "criterion_id": criterion_id,
        "verified": status == "verified",
        "status": status,
        "exit_code": result.get("exit_code"),
        "timed_out": bool(result.get("timed_out")),
        "attempts": int(item.get("attempts") or 0) if isinstance(item, dict) else 0,
        "stderr": result.get("stderr") or "",
    }


def _apply_verifier_outcome(state, criterion_id: str, *, ok: bool, exit_code: int | None, stderr: str, timeout: bool) -> None:
    criteria = state.get("criteria")
    if not isinstance(criteria, dict) or criterion_id not in criteria:
        raise ValueError(f"goal_supervisor_unknown_criterion: {criterion_id}")
    item = criteria[criterion_id]
    if not isinstance(item, dict):
        item = {}
        criteria[criterion_id] = item
    item["attempts"] = int(item.get("attempts") or 0) + 1
    if ok:
        item["status"] = "verified"
        item["last_fingerprint"] = None  # verified evidence supersedes any action fingerprint
        item["verified_by"] = "verifier"
    else:
        item["status"] = "inconclusive"
        item["last_fingerprint"] = fingerprint_evidence(item.get("observed_evidence") or [])
        item["last_error"] = stderr or ("timeout" if timeout else f"exit_{exit_code}")
        max_attempts = int(os.environ.get("GOAL_MAX_VERIFIER_ATTEMPTS", DEFAULT_MAX_VERIFIER_ATTEMPTS))
        if int(item["attempts"]) >= max_attempts:
            item["status"] = "blocked"
    item["updated_at"] = utc_now()
    _recompute_status(state)


def fingerprint_evidence(evidence_refs: list[str]) -> str:
    """A stable fingerprint of a set of evidence refs, used to detect repeated
    low-value work (a worker re-submitting the same evidence to the same criterion)."""
    normalized = [str(r).strip() for r in (evidence_refs or []) if str(r).strip()]
    blob = "\x1f".join(sorted(normalized))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def dispatch_criterion(goal_ref: str, *, route: str | None = None) -> dict[str, Any]:
    """Deterministic Phase 2 work-unit dispatch (issue #4 acceptance 4/3):
    pick the next criterion that is NOT blocked and not verified, prefer pending
    (never started), then inconclusive (started but not verified). A criterion with a
    repeated no-progress fingerprint is NOT re-dispatched on the same route: it either
    moves to the next declared alternative route, or emits ``attention_required`` with
    the exact unresolved condition. No model call."""
    goal_ref = validate_goal_ref(goal_ref)
    config = _read_json(goal_dir(BROKER_DIR, goal_ref) / "config.json", None)
    if not isinstance(config, dict):
        return {"dispatched": False, "reason": "goal_unknown"}
    ledger = _read_json(goal_dir(BROKER_DIR, goal_ref) / "ledger.json", None)
    if not isinstance(ledger, dict):
        return {"dispatched": False, "reason": "goal_unknown"}
    ordered = list(config.get("criteria") or [])

    def _pick_target():
        for criterion in ordered:
            cid = criterion.get("id")
            item = (ledger.get("criteria") or {}).get(cid)
            if not isinstance(item, dict):
                return cid, None
            status = item.get("status")
            if status == "verified" or status == "blocked":
                continue
            if status in (None, "pending"):
                return cid, None
            # inconclusive: route advance only when the fingerprint is NOT a repeat.
            fingerprint = item.get("last_fingerprint")
            item_fingerprint = fingerprint if fingerprint else None
            routes = list(criterion.get("alternative_routes") or []) or None
            return cid, routes
        return None, None

    target, alternative_routes = _pick_target()
    if target is None:
        return {"dispatched": False, "reason": "nothing_to_dispatch"}
    criterion = next((c for c in ordered if c.get("id") == target), {})
    item = (ledger.get("criteria") or {}).get(target)
    status = item.get("status") if isinstance(item, dict) else None
    fingerprint = item.get("last_fingerprint") if isinstance(item, dict) else None

    # Fingerprint repeat on an inconclusive criterion: never re-dispatch the same route.
    repeated = bool(status == "inconclusive" and fingerprint)

    if repeated and not route:
        routes = alternative_routes or []
        already_tried = [str(r) for r in (item.get("tried_routes") or [])] if isinstance(item, dict) else []
        next_route = next((r for r in routes if r not in already_tried), None)
        if next_route is not None:
            outcome = _update_ledger(
                goal_ref,
                lambda s: _mark_dispatch(s, target, route=next_route, tried=[*already_tried, next_route]),
            )
            _append_action(goal_ref, "work_dispatched", criterion_id=target, route=next_route, via="alternative_route")
            return {"dispatched": True, "criterion_id": target, "route": next_route, "via": "alternative_route"}

    if repeated:
        # No alternative route left and the fingerprint is unchanged: attention_required.
        outcome = _update_ledger(
            goal_ref,
            lambda s: _mark_attention(s, target, reason="repeated_no_progress_fingerprint"),
        )
        _append_action(
            goal_ref, "attention_required",
            criterion_id=target,
            fingerprint=fingerprint,
            routes_tried=list((item.get("tried_routes") or []) if isinstance(item, dict) else []),
        )
        return {
            "dispatched": False,
            "reason": "attention_required",
            "criterion_id": target,
            "fingerprint": fingerprint,
            "condition": item.get("last_error") if isinstance(item, dict) else None,
        }

    dispatch_route = route or "codex_exec_resume"
    outcome = _update_ledger(
        goal_ref,
        lambda s: _mark_dispatch(s, target, route=dispatch_route),
    )
    _append_action(goal_ref, "work_dispatched", criterion_id=target, route=dispatch_route)
    return {"dispatched": True, "criterion_id": target, "route": dispatch_route}


def _mark_attention(state, criterion_id: str, *, reason: str) -> None:
    criteria = state.get("criteria")
    if not isinstance(criteria, dict) or criterion_id not in criteria:
        raise ValueError(f"goal_supervisor_unknown_criterion: {criterion_id}")
    item = criteria[criterion_id]
    if not isinstance(item, dict):
        item = {}
        criteria[criterion_id] = item
    item["status"] = "inconclusive"
    item["attention"] = reason
    item["updated_at"] = utc_now()
    if state.get("status") == "in_progress":
        state["status"] = "attention_required"


def _mark_dispatch(state, criterion_id: str, *, route: str, tried: list[str] | None = None) -> None:
    criteria = state.get("criteria")
    if not isinstance(criteria, dict) or criterion_id not in criteria:
        raise ValueError(f"goal_supervisor_unknown_criterion: {criterion_id}")
    item = criteria[criterion_id]
    if not isinstance(item, dict):
        item = {}
        criteria[criterion_id] = item
    item["status"] = "running"
    item["route"] = route
    item["attempts"] = int(item.get("attempts") or 0)
    if tried is not None:
        item["tried_routes"] = tried
    item["updated_at"] = utc_now()


def enforce_budgets(goal_ref: str, *, require_telemetry: bool = True) -> dict[str, Any]:
    """Phase 2 budget enforcement (issue #4 acceptance 7):
    - total Goal budget (max_actions / total_tokens / total_seconds);
    - per-criterion budget (criterion.max_actions / total_tokens / total_seconds);
    - per-attempt budget is the criterion's attempt count vs its max_actions;
    - a terminal receipt explaining exactly where the budget was spent;
    - FAIL-CLOSED when enforcement is requested but the needed telemetry is
      unavailable (``enforcement_requires_telemetry``), unless the goal is unbudgeted.

    A breach never declares the Goal complete — it escalates (blocked for action
    exhaustion; attention_required for token/time). No model call."""
    goal_ref = validate_goal_ref(goal_ref)
    config = _read_json(goal_dir(BROKER_DIR, goal_ref) / "config.json", None)
    ledger = _read_json(goal_dir(BROKER_DIR, goal_ref) / "ledger.json", None)
    if not isinstance(config, dict) or not isinstance(ledger, dict):
        return {"enforced": False, "reason": "goal_unknown"}
    plan = config.get("budget_plan") or {}
    unbudgeted = bool(config.get("unbudgeted"))
    breaches: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {"max_actions": None, "total_tokens": None, "total_seconds": None,
                               "per_criterion": {}}

    if unbudgeted:
        return {"enforced": True, "unbudgeted": True, "breaches": [], "receipt": receipt,
                "status": ledger.get("status")}

    # Broker-owned action count (durable work signal, always available).
    criteria = ledger.get("criteria") or {}
    action_count = sum(
        int(item.get("attempts") or 0)
        for item in criteria.values()
        if isinstance(item, dict)
    )
    if plan.get("max_actions"):
        receipt["max_actions"] = action_count
        if action_count >= int(plan["max_actions"]):
            breaches.append({"kind": "max_actions", "used": action_count, "budget": int(plan["max_actions"])})

    # Per-criterion + per-attempt budgets (always computable from ledger attempts).
    for cid, item in criteria.items():
        if not isinstance(item, dict):
            continue
        c_budget = item.get("budget") or {}
        attempts = int(item.get("attempts") or 0)
        limit = c_budget.get("max_actions") if c_budget else None
        if limit and attempts >= int(limit):
            receipt["per_criterion"][cid] = {"max_actions": {"used": attempts, "budget": int(limit)}}
            breaches.append({"kind": "criterion_max_actions", "criterion_id": cid,
                             "used": attempts, "budget": int(limit)})

    # Live Codex token/time telemetry: REQUIRED when the contract has those budgets.
    wants_token = plan.get("total_tokens")
    wants_time = plan.get("total_seconds")
    usage = _read_thread_usage(config.get("codex_thread_id"))
    if (wants_token or wants_time) and usage is None:
        if require_telemetry:
            return {"enforced": False, "reason": "enforcement_requires_telemetry",
                    "breaches": [], "receipt": receipt,
                    "note": "Contract budgets token/time but Codex Goal telemetry is unavailable; fail closed."}
    if usage:
        tokens_used = usage.get("tokens_used")
        if wants_token and isinstance(tokens_used, int):
            receipt["total_tokens"] = tokens_used
            if tokens_used >= int(wants_token):
                breaches.append({"kind": "total_tokens", "used": tokens_used, "budget": int(wants_token)})
        time_used = usage.get("time_used_seconds")
        if wants_time and isinstance(time_used, int):
            receipt["total_seconds"] = time_used
            if time_used >= int(wants_time):
                breaches.append({"kind": "total_seconds", "used": time_used, "budget": int(wants_time)})
    else:
        receipt["total_tokens"] = None
        receipt["total_seconds"] = None

    if not breaches:
        return {"enforced": True, "unbudgeted": False, "breaches": [], "receipt": receipt,
                "status": ledger.get("status")}

    # Fail closed: escalate the goal so it cannot be silently completed.
    blocked = any(b["kind"] in ("max_actions", "criterion_max_actions") for b in breaches)
    outcome = _update_ledger(
        goal_ref,
        lambda s: _apply_budget_breach(s, blocked=blocked),
    )
    _append_action(goal_ref, "budget_breach", breaches=breaches, blocked=blocked, receipt=receipt)
    status = outcome.get("status") if isinstance(outcome, dict) else None
    return {"enforced": True, "unbudgeted": False, "breaches": breaches, "receipt": receipt,
            "status": status, "blocked": blocked}


def _apply_budget_breach(state, *, blocked: bool) -> None:
    if blocked:
        state["status"] = "blocked"
    elif state.get("status") == "in_progress":
        state["status"] = "attention_required"
    state["attention"] = "budget_breach"


# --- 5c. bounded work-unit packaging (issue #4 acceptance 4/7) --------------

def build_work_unit(goal_ref: str, criterion_id: str | None = None) -> dict[str, Any]:
    """Build ONE bounded, reference-based work unit for a Codex continuation (issue #4
    acceptance 4/7). It carries ONLY: the exact original objective, the current
    criterion (picked by dispatch if not given), protected boundaries, the required
    evidence references, the required verifier, and the work-unit budget. It NEVER
    replays the whole Goal transcript — compact references are sufficient. No model
    call."""
    goal_ref = validate_goal_ref(goal_ref)
    config = _read_json(goal_dir(BROKER_DIR, goal_ref) / "config.json", None)
    ledger = _read_json(goal_dir(BROKER_DIR, goal_ref) / "ledger.json", None)
    if not isinstance(config, dict) or not isinstance(ledger, dict):
        return {"built": False, "reason": "goal_unknown"}

    if criterion_id is None:
        dispatch = dispatch_criterion(goal_ref)
        if not dispatch.get("dispatched"):
            return {"built": False, "reason": dispatch.get("reason") or "nothing_to_dispatch",
                    "detail": {"criterion_id": dispatch.get("criterion_id"),
                               "fingerprint": dispatch.get("fingerprint")}}
        criterion_id = dispatch["criterion_id"]

    criterion = next((c for c in (config.get("criteria") or []) if c.get("id") == criterion_id), None)
    if criterion is None:
        return {"built": False, "reason": "unknown_criterion", "criterion_id": criterion_id}
    item = (ledger.get("criteria") or {}).get(criterion_id) or {}

    work_unit = {
        "goal_ref": goal_ref,
        "objective": config.get("objective"),
        "objective_hash": config.get("objective_hash"),
        "criterion": {
            "id": criterion.get("id"),
            "description": criterion.get("description") or "",
            "status": item.get("status") if isinstance(item, dict) else "pending",
            "required_evidence": list(criterion.get("required_evidence") or []),
            "observed_evidence": list(item.get("observed_evidence") or []) if isinstance(item, dict) else [],
            "verifier": criterion.get("verifier") or "",
            "stopping_test": criterion.get("stopping_test") or "",
            "dependencies": list(criterion.get("dependencies") or []),
        },
        "boundaries": list(config.get("boundaries") or []),
        "work_unit_budget": dict(criterion.get("budget") or {}) or dict(config.get("budget_plan") or {}),
        "route": item.get("route") if isinstance(item, dict) else None,
        "transcript_replay": False,
    }
    return {"built": True, "work_unit": work_unit, "criterion_id": criterion_id}


# --- 6. CLI helpers ---------------------------------------------------------

GOAL_HELP = (
    "Usage: agent_broker_mcp.py bridge goal ("
    "probe | contract --objective <text> --criteria <json> [--budgets <json>] [--unbudgeted] | "
    "create --objective <text> --criteria <json> [--boundaries <json>] [--budgets <json>] [--unbudgeted] [--thread <id>] | "
    "list | status <goal_ref> [--recent <n>] | "
    "evidence <goal_ref> <criterion> <evidence...> [--status pending|running|inconclusive|blocked] | "
    "dispatch <goal_ref> [--route <r>] | work-unit <goal_ref> [<criterion>] | "
    "verify <goal_ref> <criterion> [--timeout <seconds>] | "
    "enforce <goal_ref> [--require-telemetry] | complete <goal_ref>)"
)


def handle_goal_cli(argv: list[str]) -> int:
    """bridge goal subcommand dispatch. Deterministic; never calls a model."""
    if not argv or argv[0] in {"help", "-h", "--help"}:
        print(GOAL_HELP)
        return 0
    command = argv[0]
    if command == "probe":
        print(json.dumps(probe_goal_capabilities(), ensure_ascii=True, indent=2))
        return 0
    if command == "contract":
        result = _contract_from_args(argv[1:], create=False)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("valid") else 1
    if command == "create":
        result = _contract_from_args(argv[1:], create=True)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("created") else 1
    if command == "list":
        print(json.dumps(list_goals(), ensure_ascii=True, indent=2))
        return 0
    if command == "status":
        if len(argv) < 2:
            raise ValueError("status requires <goal_ref>")
        recent = 10
        if "--recent" in argv:
            index = argv.index("--recent")
            if index + 1 < len(argv):
                try:
                    recent = int(argv[index + 1])
                except ValueError:
                    pass
        print(json.dumps(get_goal_status(argv[1], recent_actions=recent), ensure_ascii=True, indent=2))
        return 0
    if command == "evidence":
        if len(argv) < 4:
            raise ValueError("evidence requires <goal_ref> <criterion> <evidence...>")
        status_hint = None
        args = argv[3:]
        if "--status" in args:
            index = args.index("--status")
            if index + 1 < len(args):
                status_hint = args[index + 1]
                args = args[:index] + args[index + 2:]
        result = record_evidence(argv[1], argv[2], args, status_hint=status_hint)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("recorded") else 1
    if command == "dispatch":
        if len(argv) < 2:
            raise ValueError("dispatch requires <goal_ref> [--route <r>]")
        route = None
        if "--route" in argv:
            index = argv.index("--route")
            if index + 1 < len(argv):
                route = argv[index + 1]
        print(json.dumps(dispatch_criterion(argv[1], route=route), ensure_ascii=True, indent=2))
        return 0
    if command == "work-unit":
        if len(argv) < 2:
            raise ValueError("work-unit requires <goal_ref> [<criterion>]")
        print(json.dumps(build_work_unit(argv[1], argv[2] if len(argv) > 2 else None), ensure_ascii=True, indent=2))
        return 0
    if command == "verify":
        if len(argv) < 3:
            raise ValueError("verify requires <goal_ref> <criterion> [--timeout <seconds>]")
        timeout = None
        if "--timeout" in argv:
            index = argv.index("--timeout")
            if index + 1 < len(argv):
                try:
                    timeout = float(argv[index + 1])
                except ValueError:
                    pass
        print(json.dumps(run_verifier(argv[1], argv[2], timeout_seconds=timeout), ensure_ascii=True, indent=2))
        return 0
    if command == "enforce":
        if len(argv) < 2:
            raise ValueError("enforce requires <goal_ref> [--require-telemetry]")
        print(json.dumps(
            enforce_budgets(argv[1], require_telemetry="--require-telemetry" in argv),
            ensure_ascii=True, indent=2,
        ))
        return 0
    if command == "complete":
        if len(argv) < 2:
            raise ValueError("complete requires <goal_ref>")
        print(json.dumps(complete_goal(argv[1]), ensure_ascii=True, indent=2))
        return 0
    raise ValueError(f"unknown goal subcommand: {command}")


def _contract_from_args(argv: list[str], create: bool) -> dict[str, Any]:
    def arg_value(name: str) -> str | None:
        if name not in argv:
            return None
        index = argv.index(name)
        return argv[index + 1] if index + 1 < len(argv) else None

    objective = arg_value("--objective")
    criteria_json = arg_value("--criteria")
    boundaries_json = arg_value("--boundaries")
    budgets_json = arg_value("--budgets")
    unbudgeted = "--unbudgeted" in argv
    thread_id = arg_value("--thread")

    if objective is None or criteria_json is None:
        raise ValueError("contract/create requires --objective and --criteria")
    try:
        criteria = json.loads(criteria_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--criteria must be a JSON array: {exc}") from exc
    if not isinstance(criteria, list):
        raise ValueError("--criteria must be a JSON array")
    boundaries: list[str] | None = None
    if boundaries_json:
        boundaries = json.loads(boundaries_json)
        if not isinstance(boundaries, list):
            raise ValueError("--boundaries must be a JSON array")
    budgets: dict[str, Any] | None = None
    if budgets_json:
        budgets = json.loads(budgets_json)
        if not isinstance(budgets, dict):
            raise ValueError("--budgets must be a JSON object")

    if create:
        return create_goal(
            objective,
            criteria,
            boundaries=boundaries,
            budgets=budgets,
            unbudgeted=unbudgeted,
            codex_thread_id=thread_id,
        )
    return validate_goal_contract(objective, criteria, boundaries, budgets, unbudgeted)
