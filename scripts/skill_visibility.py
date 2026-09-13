"""Shared report-only visibility classification for installed Skills."""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Iterable

REGISTERED = "registered"
KNOWN_ALLOWED_LOCAL = "known_allowed_local"
UNMANAGED_SKILL = "unmanaged_skill"
PASS = "PASS"
INDETERMINATE = "INDETERMINATE"

# Deliberately explicit: this package is device-local and is not in skills.json.
KNOWN_ALLOWED_LOCAL_SKILLS = frozenset({"weekly-work-summary"})


def classify_skill_visibility(
    name: str,
    registered_names: Iterable[str],
    known_allowed_local_names: Iterable[str] = KNOWN_ALLOWED_LOCAL_SKILLS,
) -> str:
    """Classify one installed skill without authorizing any filesystem action."""
    registered = set(registered_names)
    if name in registered:
        return REGISTERED
    if name in set(known_allowed_local_names):
        return KNOWN_ALLOWED_LOCAL
    return UNMANAGED_SKILL


# Short alias for callers/tests that only need the classifier.
classify_skill = classify_skill_visibility


def _installed_skill_names(skill_root: Path) -> tuple[list[str], list[str]]:
    """Return top-level installed skill names and any scan errors.

    A directory is a Skill candidate only when it contains a regular ``SKILL.md``.
    Missing marker files are normal non-Skill entries; filesystem errors are not
    silently converted into absence and make the caller's result indeterminate.
    """
    try:
        root_stat = skill_root.stat()
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"skill visibility scan failed for {skill_root}: {exc}"]

    if not stat.S_ISDIR(root_stat.st_mode):
        return [], [f"skill visibility scan root is not a directory: {skill_root}"]

    names: list[str] = []
    errors: list[str] = []
    try:
        entries = sorted(skill_root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return [], [f"skill visibility scan failed for {skill_root}: {exc}"]

    for entry in entries:
        try:
            if not stat.S_ISDIR(entry.stat().st_mode):
                continue
            marker = entry / "SKILL.md"
            try:
                marker_stat = marker.stat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(marker_stat.st_mode):
                names.append(entry.name)
        except OSError as exc:
            errors.append(f"skill visibility scan failed for {entry}: {exc}")

    return names, errors


def scan_skill_visibility(
    skill_root: Path,
    registered_names: Iterable[str],
    known_allowed_local_names: Iterable[str] = KNOWN_ALLOWED_LOCAL_SKILLS,
) -> dict[str, Any]:
    """Scan installed Skills and report all three visibility categories.

    This function is intentionally report-only: it never copies, deletes, or
    changes a filesystem entry. ``INDETERMINATE`` means the scan could not prove
    the complete inventory; callers must not silently present that as PASS.
    """
    registered = set(registered_names)
    allowed_local = set(known_allowed_local_names)
    names, errors = _installed_skill_names(Path(skill_root))
    categories = {
        REGISTERED: [],
        KNOWN_ALLOWED_LOCAL: [],
        UNMANAGED_SKILL: [],
    }
    for name in names:
        category = classify_skill_visibility(name, registered, allowed_local)
        categories[category].append(name)

    status = INDETERMINATE if errors else PASS
    return {
        "status": status,
        "scan_status": status,
        "root": str(skill_root),
        "registered": categories[REGISTERED],
        "known_allowed_local": categories[KNOWN_ALLOWED_LOCAL],
        "unmanaged_skill": categories[UNMANAGED_SKILL],
        "skills": [
            {"name": name, "category": classify_skill_visibility(name, registered, allowed_local)}
            for name in names
        ],
        "errors": errors,
    }
