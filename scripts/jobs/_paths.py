"""_paths.py — instance-root resolution for the jobs package (instance-contract-v1)."""
from __future__ import annotations

import os
from pathlib import Path


def _is_instance_root(p: Path) -> bool:
    # instance-contract-v1: instance.yaml or state/ marker
    return (p / "instance.yaml").is_file() or (p / "state").is_dir()


def instance_root() -> Path:
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
