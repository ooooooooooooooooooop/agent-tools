"""checkpoint_ext.py — Machine-extended deterministic checkpoints building upon checkpoint.py."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional, Tuple

from .models import MachineCheckpoint


def compute_sha256(path: Path) -> str:
    """Compute sha256 hash of a file on disk."""
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class MachineCheckpointManager:
    """Handles serialization, disk persistence, and deterministic validation of machine checkpoints."""

    def __init__(self, checkpoints_root: Optional[Path] = None) -> None:
        self.root = checkpoints_root or Path.home() / ".personal-ai" / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def checkpoint_path(self, job_id: str, attempt_id: str) -> Path:
        safe_job = "".join(c if c.isalnum() or c in "-_." else "_" for c in job_id)
        safe_att = "".join(c if c.isalnum() or c in "-_." else "_" for c in attempt_id)
        job_dir = self.root / safe_job
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir / f"checkpoint_{safe_att}.json"

    def write_checkpoint(self, checkpoint: MachineCheckpoint) -> Path:
        """Write machine checkpoint atomically."""
        path = self.checkpoint_path(checkpoint.job_id, checkpoint.attempt_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(checkpoint.to_json() + "\n", encoding="utf-8")
        tmp.replace(path)
        return path

    def load_checkpoint(self, job_id: str, attempt_id: str) -> Optional[MachineCheckpoint]:
        """Load machine checkpoint from disk."""
        path = self.checkpoint_path(job_id, attempt_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MachineCheckpoint.from_dict(data)
        except Exception:
            return None

    def validate_checkpoint_for_resume(
        self,
        checkpoint: MachineCheckpoint,
        *,
        verify_inputs: bool = True,
    ) -> Tuple[bool, str]:
        """Validate whether this checkpoint can be deterministically resumed.

        Verifies:
        1. Checkpoint version is supported.
        2. Authorized write root exists and is valid.
        3. All input source files on disk match the recorded source_hashes byte-for-byte.
        Returns:
            (True, "VALID") or (False, "REJECTION_REASON")
        """
        if checkpoint.checkpoint_version < 1:
            return False, f"unsupported checkpoint version: {checkpoint.checkpoint_version}"

        # 1. Authorized root check
        if checkpoint.authorized_root:
            auth_p = Path(checkpoint.authorized_root)
            if not auth_p.exists():
                return False, f"authorized_root does not exist on this machine: {checkpoint.authorized_root}"

        # 2. Source input bytes stability check
        if verify_inputs and checkpoint.source_hashes:
            for rel_or_abs, exp_hash in checkpoint.source_hashes.items():
                p = Path(rel_or_abs)
                if not p.is_absolute() and checkpoint.authorized_root:
                    p = Path(checkpoint.authorized_root) / p
                if not p.is_file():
                    return False, f"source input file missing on disk: {p}"
                act_hash = compute_sha256(p)
                if act_hash != exp_hash:
                    return False, f"source input file modified on disk: {p} (expected {exp_hash}, got {act_hash})"

        return True, "VALID"
