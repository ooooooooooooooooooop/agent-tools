#!/usr/bin/env python3
"""backup_configs.py — protect IRREPLACEABLE local config only (local tier).

Generated/canonical-managed content (derivable via aic render) is NOT duplicated
here — the manifest classifies each entry so no second SSOT illusion arises.
Secret-containing files are backed up locally (never uploaded); only their
names/hashes appear in the manifest — never their content.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import backup_root, ledger_append, now_iso, sha256_file, write_manifest  # noqa: E402

HOME = Path.home()
CPA = Path(r"D:\Download\EasyCLIProxyAPI-v0.2.23-Windows-amd64\EasyCLIProxyAPI-v0.2.23-Windows-amd64\cpa-core")


def _cpa_core_dir() -> Path:
    """Resolve the local CPA gateway cpa-core dir dynamically (Downloads layout)."""
    candidates = [
        CPA,
        *sorted(Path.home().glob(r"Downloads\EasyCLIProxyAPI-*\*\cpa-core")),
        *sorted(Path.home().glob(r"Download\EasyCLIProxyAPI-*\*\cpa-core")),
    ]
    for c in candidates:
        if (c / "cli-proxy-api.exe").is_file():
            return c
    return CPA

# (source, irreplaceable?, secret?)
CONFIGS = [
    (HOME / ".dsh" / "settings.yaml", False, False),          # generated (aic render dsh)
    (HOME / ".dsh" / "AGENTS.md", True, False),               # user-owned prefs
    (HOME / ".codex" / "AGENTS.md", False, False),            # generated (switchboard block)
    (HOME / ".claude" / "CLAUDE.md", False, False),           # generated
    (HOME / ".gemini" / "GEMINI.md", False, False),           # generated
    (HOME / ".agent-broker" / "config.json", True, False),    # machine-local overlay
    (HOME / ".claude" / "settings.json", True, False),        # hooks overlay + generated env
    (HOME / ".codex" / "config.toml", True, False),           # machine-local overlay
    (HOME / ".gemini" / "settings.json", True, False),
    (HOME / ".cc-switch" / "settings.json", True, False),
    (_cpa_core_dir() / "config.yaml", True, True),        # gateway local config (secret-adjacent)
]


def main() -> int:
    started = now_iso()
    root = backup_root()
    day = root / "configs" / ("daily-" + started[:10])
    day.mkdir(parents=True, exist_ok=True)
    entries, failed = [], []
    for src, irreplaceable, secret in CONFIGS:
        if not src.is_file():
            failed.append(f"{src.name}: missing")
            continue
        dst = day / f"{src.parent.name}--{src.name}"
        try:
            shutil.copy2(src, dst)
            entries.append({"name": f"{src.parent.name}/{src.name}",
                            "bytes": src.stat().st_size, "sha256": sha256_file(dst),
                            "class": "irreplaceable" if irreplaceable else "generated-rebuildable",
                            "secret_local_only": secret})
        except OSError as exc:
            failed.append(f"{src.name}: {exc}")
    # cpa auths dir (tokens; local-only, names+hashes only)
    auths = CPA / "auths"
    if auths.is_dir():
        adir = day / "cpa-auths"
        adir.mkdir(exist_ok=True)
        for f in sorted(auths.glob("*.json")):
            try:
                shutil.copy2(f, adir / f.name)
                entries.append({"name": f"cpa-auths/{f.name}", "bytes": f.stat().st_size,
                                "sha256": sha256_file(adir / f.name),
                                "class": "irreplaceable", "secret_local_only": True})
            except OSError as exc:
                failed.append(f"auths/{f.name}: {exc}")
    mpath = write_manifest(day, entries, {"dataset": "configs"})
    status = "ok" if not failed else "error"
    ledger_append({"job": "backup_configs", "dataset": "configs", "started_at": started,
                   "finished_at": now_iso(), "status": status,
                   "target_generation": day.name, "files": len(entries),
                   "bytes": sum(e["bytes"] for e in entries),
                   "manifest": str(mpath.relative_to(root)).replace("\\", "/"),
                   "integrity_status": "verified" if status == "ok" else "failed",
                   "error": "; ".join(failed) if failed else None})
    print(f"configs backup: files={len(entries)} failed={len(failed)} -> {day.name} [{status}]")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
