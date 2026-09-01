"""Render curated Personal AI policies into generated static instruction blocks.

The policy remains canonical in ``personal-ai-state/state/preferences.md`` and
``registry/autonomous-execution-governance.yaml``. Harness instruction files
contain checksum-protected generated projections only.

Legacy single-block API (CONTINUOUS_CAPABILITY_ADOPTION) is preserved as
wrappers; generic multi-block helpers are used by the AUTONOMOUS_EXECUTION_GOVERNANCE
projection and by aic render/diff/apply.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

POLICY_ID = "CONTINUOUS_CAPABILITY_ADOPTION"
_BLOCK_SLUG = POLICY_ID.lower().replace("_", "-")
BLOCK_END = f"<!-- aic:{_BLOCK_SLUG}:end -->"
BLOCK_START_RE = re.compile(
    rf"<!-- aic:{re.escape(_BLOCK_SLUG)}:begin sha256=([0-9a-f]{{64}}) -->"
)


def preferences_path(state_root: str | Path | None = None) -> Path:
    root = Path(
        state_root
        or os.environ.get("PERSONAL_AI_STATE")
        or (Path.home() / "personal-ai-state")
    )
    return root / "state" / "preferences.md"


def extract_policy(markdown: str, policy_id: str = POLICY_ID) -> str:
    heading = re.compile(rf"(?m)^##\s+{re.escape(policy_id)}\s*$")
    matches = list(heading.finditer(markdown))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one ## {policy_id} section, found {len(matches)}")
    start = matches[0].start()
    next_heading = re.compile(r"(?m)^##\s+").search(markdown, matches[0].end())
    end = next_heading.start() if next_heading else len(markdown)
    return markdown[start:end].strip() + "\n"


def load_policy(state_root: str | Path | None = None) -> str:
    path = preferences_path(state_root)
    return extract_policy(path.read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------- generic multi-block

def _markers(slug: str) -> tuple[str, re.Pattern[str]]:
    end = f"<!-- aic:{slug}:end -->"
    start_re = re.compile(rf"<!-- aic:{re.escape(slug)}:begin sha256=([0-9a-f]{{64}}) -->")
    return end, start_re


def render_managed_block_for(slug: str, canonical_text: str) -> str:
    end, _ = _markers(slug)
    canonical = canonical_text.strip() + "\n"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return (
        f"<!-- aic:{slug}:begin sha256={digest} -->\n"
        f"{canonical}{end}"
    )


def _block_parts_for(slug: str, text: str):
    end, start_re = _markers(slug)
    start = start_re.search(text)
    if not start:
        return None
    body_start = start.end()
    if text[body_start : body_start + 1] == "\n":
        body_start += 1
    end_idx = text.find(end, body_start)
    if end_idx < 0:
        raise ValueError(f"managed {slug} block has no end marker")
    return start, end_idx, text[body_start:end_idx]


def inspect_managed_block_for(slug: str, text: str, canonical_text: str) -> tuple[bool, str]:
    try:
        parts = _block_parts_for(slug, text)
    except ValueError as exc:
        return False, f"INVALID: {exc}"
    if not parts:
        return False, "MISSING"
    start, _end, body = parts
    checksum_ok = start.group(1) == hashlib.sha256(body.encode("utf-8")).hexdigest()
    if not checksum_ok:
        return False, "CHECKSUM_MISMATCH"
    expected = canonical_text.strip() + "\n"
    return (body == expected, "current" if body == expected else "STALE")


def update_managed_block_for(slug: str, existing: str, canonical_text: str) -> tuple[str, str]:
    rendered = render_managed_block_for(slug, canonical_text)
    parts = _block_parts_for(slug, existing)
    if parts:
        _ok, observed = inspect_managed_block_for(slug, existing, canonical_text)
        if observed == "CHECKSUM_MISMATCH":
            raise ValueError(f"managed {slug} block was edited")
        start, end_idx, _body = parts
        end, _ = _markers(slug)
        updated = existing[: start.start()] + rendered + existing[end_idx + len(end) :]
    else:
        separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        updated = existing + separator + rendered + "\n"
    return updated, "unchanged" if updated == existing else "updated"


# ---------------------------------------------------------------- legacy single-block API

def render_managed_block(policy_text: str) -> str:
    return render_managed_block_for(_BLOCK_SLUG, policy_text)


def _block_parts(text: str):
    return _block_parts_for(_BLOCK_SLUG, text)


def inspect_managed_block(text: str, policy_text: str) -> tuple[bool, str]:
    return inspect_managed_block_for(_BLOCK_SLUG, text, policy_text)


def update_managed_block_text(existing: str, policy_text: str) -> tuple[str, str]:
    return update_managed_block_for(_BLOCK_SLUG, existing, policy_text)