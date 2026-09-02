"""provisioner.py — Shared Personal AI Worktree Provisioner.

Single shared lifecycle owner for Git worktrees across DSH, Codex, Claude Code, and Gemini.
Lifecycle methods: CREATE, INSPECT, COMPLETE, PRESERVE, CLEANUP, ORPHAN_RECOVERY, PROVENANCE.
AUTO_MERGE is strictly forbidden: completed worktrees yield diff and validation artifacts.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path.home() / ".dsh" / "worktrees"


@dataclass
class WorktreeRecord:
    task_id: str
    base_repo: str
    base_commit: str
    worktree_path: str
    branch_name: str
    created_at: str
    status: str  # ACTIVE | COMPLETED | FAILED | PRESERVED | CLEANED
    harness: str = "shared"
    diff_stat: str = ""
    commit_sha: str = ""
    additional_dirs: list[str] = field(default_factory=list)
    write_scopes: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _git(cwd: Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        res = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return res.returncode, (res.stdout + res.stderr).strip()
    except Exception as exc:
        return -1, str(exc)


class WorktreeProvisioner:
    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = (state_dir or DEFAULT_STATE_DIR).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_file = self.state_dir / "worktrees.json"
        self.provenance_file = self.state_dir / "provenance.jsonl"

    def _load_ledger(self) -> dict[str, dict[str, Any]]:
        if not self.ledger_file.is_file():
            return {}
        try:
            return json.loads(self.ledger_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_ledger(self, data: dict[str, dict[str, Any]]) -> None:
        tmp = self.ledger_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.ledger_file)

    def _append_provenance(self, record: dict[str, Any]) -> None:
        try:
            line = json.dumps(record, ensure_ascii=False) + "\n"
            with self.provenance_file.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def create(
        self,
        base_repo: Path,
        task_id: str,
        *,
        base_commit: str = "HEAD",
        branch_name: str | None = None,
        harness: str = "shared",
        additional_dirs: list[Path] | None = None,
        write_scopes: list[Path] | None = None,
    ) -> WorktreeRecord:
        """Create an isolated git worktree for the given task."""
        repo = base_repo.resolve()
        rc, head_out = _git(repo, "rev-parse", base_commit)
        if rc != 0:
            raise RuntimeError(f"failed to resolve base_commit '{base_commit}': {head_out}")
        resolved_commit = head_out.strip()

        safe_task = re.sub(r"[^A-Za-z0-9_-]", "-", task_id)
        branch = branch_name or f"ai-worktree-{safe_task}"
        wt_dir = repo.parent / f"{repo.name}__worktree_{safe_task}"

        if wt_dir.exists():
            # If already exists, verify ledger
            ledger = self._load_ledger()
            if task_id in ledger and ledger[task_id]["status"] == "ACTIVE":
                return WorktreeRecord(**ledger[task_id])
            shutil.rmtree(wt_dir, ignore_errors=True)

        # Create branch and worktree
        rc, out = _git(repo, "worktree", "add", "-B", branch, str(wt_dir), resolved_commit)
        if rc != 0:
            raise RuntimeError(f"git worktree add failed ({rc}): {out}")

        record = WorktreeRecord(
            task_id=task_id,
            base_repo=str(repo),
            base_commit=resolved_commit,
            worktree_path=str(wt_dir),
            branch_name=branch,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE",
            harness=harness,
            additional_dirs=[str(d.resolve()) for d in (additional_dirs or [])],
            write_scopes=[str(d.resolve()) for d in (write_scopes or [wt_dir])],
        )

        ledger = self._load_ledger()
        ledger[task_id] = record.to_dict()
        self._save_ledger(ledger)
        self._append_provenance({**record.to_dict(), "event": "CREATE"})
        return record

    def inspect(self, task_id: str) -> dict[str, Any]:
        """Inspect the current state, status, diff, and commits of a managed worktree."""
        ledger = self._load_ledger()
        if task_id not in ledger:
            raise KeyError(f"task_id '{task_id}' not found in managed worktrees ledger")
        data = ledger[task_id]
        wt_path = Path(data["worktree_path"])
        if not wt_path.exists():
            return {**data, "exists_on_disk": False}

        rc, stat_out = _git(wt_path, "status", "--short")
        rc, diff_stat = _git(wt_path, "diff", "--stat", data["base_commit"])
        rc, head_out = _git(wt_path, "rev-parse", "HEAD")
        return {
            **data,
            "exists_on_disk": True,
            "dirty": bool(stat_out.strip()),
            "current_head": head_out.strip() if rc == 0 else "unknown",
            "diff_stat": diff_stat,
        }

    def complete(
        self,
        task_id: str,
        *,
        success: bool = True,
        notes: str = "",
    ) -> WorktreeRecord:
        """Mark worktree task complete, collect diff and validation.

        AUTO_MERGE is strictly FORBIDDEN.
        """
        ledger = self._load_ledger()
        if task_id not in ledger:
            raise KeyError(f"task_id '{task_id}' not found in managed worktrees ledger")

        rec_data = ledger[task_id]
        wt_path = Path(rec_data["worktree_path"])
        diff_stat = ""
        head_commit = ""

        if wt_path.exists():
            _, diff_stat = _git(wt_path, "diff", "--stat", rec_data["base_commit"])
            rc, head_out = _git(wt_path, "rev-parse", "HEAD")
            if rc == 0:
                head_commit = head_out.strip()

        status = "COMPLETED" if success else "FAILED"
        rec_data["status"] = status
        rec_data["diff_stat"] = diff_stat
        rec_data["commit_sha"] = head_commit
        rec_data["notes"] = notes

        record = WorktreeRecord(**rec_data)
        ledger[task_id] = record.to_dict()
        self._save_ledger(ledger)
        self._append_provenance({**record.to_dict(), "event": "COMPLETE", "auto_merge": False})
        return record

    def preserve(self, task_id: str, reason: str = "") -> WorktreeRecord:
        """Explicitly preserve a worktree for human inspection without deleting."""
        ledger = self._load_ledger()
        if task_id not in ledger:
            raise KeyError(f"task_id '{task_id}' not found")
        rec_data = ledger[task_id]
        rec_data["status"] = "PRESERVED"
        rec_data["notes"] = reason or rec_data.get("notes", "")
        record = WorktreeRecord(**rec_data)
        ledger[task_id] = record.to_dict()
        self._save_ledger(ledger)
        self._append_provenance({**record.to_dict(), "event": "PRESERVE", "reason": reason})
        return record

    def cleanup(self, task_id: str, *, force: bool = False) -> bool:
        """Safely remove a completed or explicitly deleted worktree.

        Fails closed on uncompleted or preserved worktrees unless force=True.
        """
        ledger = self._load_ledger()
        if task_id not in ledger:
            return False
        rec_data = ledger[task_id]
        if rec_data["status"] not in ("COMPLETED", "CLEANED") and not force:
            raise RuntimeError(
                f"refusing to cleanup worktree for task '{task_id}' in state '{rec_data['status']}'; "
                f"must be COMPLETED or force=True"
            )

        wt_path = Path(rec_data["worktree_path"])
        repo_path = Path(rec_data["base_repo"])

        if repo_path.exists():
            _git(repo_path, "worktree", "remove", "--force", str(wt_path))
            _git(repo_path, "branch", "-D", rec_data["branch_name"])

        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)

        rec_data["status"] = "CLEANED"
        self._save_ledger(ledger)
        self._append_provenance({**rec_data, "event": "CLEANUP"})
        return True

    def list_managed_worktrees(self, base_repo: Path | None = None) -> list[WorktreeRecord]:
        """Enumerate managed worktrees and reconcile with filesystem/git reality."""
        ledger = self._load_ledger()
        results: list[WorktreeRecord] = []
        for task_id, data in ledger.items():
            if base_repo and Path(data["base_repo"]).resolve() != base_repo.resolve():
                continue
            results.append(WorktreeRecord(**data))
        return results

    def reconcile_orphans(self, base_repo: Path) -> list[dict[str, Any]]:
        """Discover unmanaged / orphaned git worktrees and register them as candidates."""
        repo = base_repo.resolve()
        rc, out = _git(repo, "worktree", "list", "--porcelain")
        if rc != 0:
            return []

        ledger = self._load_ledger()
        known_paths = {Path(d["worktree_path"]).resolve() for d in ledger.values()}

        orphans = []
        current_wt = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                current_wt = Path(line.split(" ", 1)[1]).resolve()
            elif line.startswith("HEAD ") and current_wt and current_wt != repo:
                if current_wt not in known_paths and current_wt.exists():
                    orphan_info = {
                        "worktree_path": str(current_wt),
                        "head": line.split(" ", 1)[1],
                        "status": "ORPHAN_DISCOVERED",
                    }
                    orphans.append(orphan_info)
                    self._append_provenance(orphan_info)
        return orphans
