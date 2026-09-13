#!/usr/bin/env python3
"""soul_release_check.py — mechanical release-anchor verification for soul/.

Verifies manifest.json against git reality:
  1. soul_version == release.tag minus "soul-" prefix
  2. release.tag resolves to an existing git tag
  3. release.content_commit is a full 40-hex SHA that resolves to a commit
  4. content_commit is an ancestor of the tagged commit
  5. recomputed payload_digest == manifest payload_digest

Modes: default (pre-release) treats a not-yet-created release tag as a
warning — used by PR/regression gates so soul-N+1 development does not
deadlock on its own tag. `--require-tag` makes a missing tag a hard FAIL;
run it after tagging as the release-time check.

Digest rule (canonical): sha256 over sorted rel-posix-path bytes +
newline-normalized file bytes (CRLF/CR -> LF, stable across autocrlf
checkouts) for every file under soul/ except manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOUL = ROOT / "soul"
MANIFEST = SOUL / "manifest.json"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def payload_digest(soul_dir: Path) -> str:
    h = hashlib.sha256()
    files = sorted(
        p for p in soul_dir.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    )
    for p in files:
        h.update(p.relative_to(soul_dir).as_posix().encode("utf-8"))
        h.update(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
    return "sha256:" + h.hexdigest()


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def main() -> int:
    require_tag = "--require-tag" in sys.argv
    fails: list[str] = []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rel = manifest.get("release") or {}
    soul_version = manifest.get("soul_version", "")
    tag = rel.get("tag", "")
    content_commit = rel.get("content_commit") or ""
    stored_digest = rel.get("payload_digest", "")

    if f"soul-{soul_version}" != tag:
        fails.append(
            f"version/tag mismatch: soul_version={soul_version!r} vs release.tag={tag!r}")

    tag_ref = git("rev-parse", "--verify", f"refs/tags/{tag}")
    if tag_ref.returncode != 0:
        if require_tag:
            fails.append(f"release.tag {tag!r} does not resolve to a git tag")
        else:
            print(f"WARN release.tag {tag!r} not yet created (pre-release state)")
        tag_commit = None
    else:
        tag_commit = git("rev-list", "-n", "1", tag).stdout.strip()

    if not FULL_SHA.match(content_commit):
        fails.append(
            f"content_commit must be a full 40-hex SHA, got {content_commit!r}")
    else:
        if git("cat-file", "-e", f"{content_commit}^{{commit}}").returncode != 0:
            fails.append(f"content_commit {content_commit} does not resolve to a commit")
        elif tag_commit:
            anc = git("merge-base", "--is-ancestor", content_commit, tag_commit)
            if anc.returncode != 0:
                fails.append(
                    f"content_commit {content_commit} is not an ancestor of tag {tag}")

    recomputed = payload_digest(SOUL)
    if stored_digest != recomputed:
        fails.append(
            f"payload_digest mismatch: stored={stored_digest} recomputed={recomputed}")

    if fails:
        for f in fails:
            print(f"FAIL {f}")
        return 1
    print(f"PASS soul release anchor: tag={tag} content_commit={content_commit[:12]} "
          f"digest={recomputed[:19]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
