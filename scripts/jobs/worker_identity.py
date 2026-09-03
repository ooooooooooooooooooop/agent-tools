"""worker_identity.py — Worker process identity, birth-time tracking, and PID-reuse safe liveness."""
from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class WorkerIdentity:
    pid: int
    process_start_time: str
    host: str
    worker_type: str = "process"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerIdentity:
        return cls(
            pid=int(data.get("pid", 0)),
            process_start_time=str(data.get("process_start_time", "")),
            host=str(data.get("host", "")),
            worker_type=str(data.get("worker_type", "process")),
        )

    @classmethod
    def from_json(cls, json_str: str) -> WorkerIdentity:
        return cls.from_dict(json.loads(json_str))


def current_device_id() -> str:
    """Return canonical device / host identifier."""
    return platform.node().lower() or os.environ.get("COMPUTERNAME", "unknown").lower()


def get_process_creation_time(pid: int) -> Optional[str]:
    """Retrieve the process creation time on Windows/Linux to disambiguate PID reuse."""
    if os.name == "nt":
        # Query Win32_Process via powershell / CIM
        try:
            cmd = f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | Select-Object -ExpandProperty CreationDate"
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
            val = res.stdout.strip()
            return val if val else None
        except Exception:
            return None
    else:
        # On POSIX, use /proc/<pid>/stat starttime
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            try:
                parts = stat_path.read_text().split()
                return parts[21] if len(parts) > 21 else None
            except Exception:
                return None
    return None


def create_worker_identity(pid: int, worker_type: str = "process", custom_start_time: Optional[str] = None) -> WorkerIdentity:
    """Construct a full WorkerIdentity bound to the current host, PID, and process start time."""
    start_time = custom_start_time or get_process_creation_time(pid) or str(os.path.getmtime(__file__))
    return WorkerIdentity(
        pid=pid,
        process_start_time=start_time,
        host=current_device_id(),
        worker_type=worker_type,
    )


def is_worker_alive(identity: WorkerIdentity) -> bool:
    """Determine whether the specified worker is truly alive without false positives from PID reuse.

    Returns False if:
    - The worker was launched on a different device (device mismatch).
    - The PID does not exist in the OS process table.
    - The PID exists but belongs to a newly recycled process with a different creation time.
    """
    if identity.host.lower() != current_device_id():
        # Foreign machine / restored backup on new device: worker is not alive here
        return False

    current_start_time = get_process_creation_time(identity.pid)
    if current_start_time is None:
        # Process does not exist
        return False

    # Disambiguate PID reuse: start times must match
    return current_start_time == identity.process_start_time
