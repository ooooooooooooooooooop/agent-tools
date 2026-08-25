#!/usr/bin/env python3
"""Export or restore DSH user config as a sanitized skeleton package.

The command never writes to a destination unless --apply is given, and never
deletes destination files. It hard-excludes credentials and runtime state. Use
``--check`` for a read-only comparison and ``--apply`` only when the destination
is intentional.

Reports PASS/PARTIAL/FAIL and prints a manifest digest. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

DEFAULT_INCLUDE = ("AGENTS.md", "settings.yaml")
OPTIONAL_INCLUDE = ("profiles", ".agent-presets", "patches")
HARD_EXCLUDE_DIRS = {"sessions", "storages", "skills", "__pycache__", "node_modules"}
HARD_EXCLUDE_NAMES = {".credentials.yaml"}
FORBIDDEN_SNIPPETS = {".jsonl", ".log"}
SECRET_VALUE_RE = None  # placeholder; kept simple to avoid over-engineering

# Device-path placeholders, longest value first so nested paths (e.g. ~/.dsh)
# are replaced before their parent (~). Export with --template replaces device
# values with placeholders; apply renders them back for the target device.
TEMPLATE_CONFIG = (
    ("{{DSH_HOME}}", lambda: str(home_dir())),
    ("{{DESKTOP}}", lambda: str(Path.home() / "Desktop")),
    ("{{HOME}}", lambda: str(Path.home())),
)
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".txt", ".json", ".js", ".py", ".ts", ".cfg", ".conf", ".ini", ".toml"}


def home_dir() -> Path:
    return Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))


def _escaped_win(value: str) -> str:
    """Backslash-doubled form used by files that store Windows paths escaped."""
    return value.replace("\\", "\\\\")


def render_export_text(text: str) -> tuple[str, dict]:
    """Replace known device paths with placeholders.

    Returns ``(rendered_text, usage)`` where ``usage`` maps each placeholder to
    its export-time device value, so an archive records exactly what it
    templated and can warn on same-device restore.
    """
    usage: dict[str, dict] = {}
    for placeholder, resolver in TEMPLATE_CONFIG:
        device = resolver()
        if not device:
            continue
        for variant in (device, _escaped_win(device)):
            if variant in text:
                text = text.replace(variant, placeholder)
        if placeholder in text:
            usage[placeholder] = {"export_value": device}
    return text, usage


def render_apply_text(text: str) -> str:
    """Render placeholders back to this device's values."""
    for placeholder, resolver in TEMPLATE_CONFIG:
        text = text.replace(placeholder, resolver())
    return text


def sensible_value(name: str) -> bool:
    """Treat an apiKeyEnv/secret value as safe when it looks like a var NAME."""
    n = name.lower()
    return n.isupper() or "_" in name or n in {"null", ""}


def scan_sensitive(pkg_root: Path) -> list[str]:
    issues: list[str] = []
    for path in pkg_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in HARD_EXCLUDE_DIRS for part in path.parts):
            issues.append(f"runtime dir present: {path}")
            continue
        if path.name in HARD_EXCLUDE_NAMES:
            issues.append(f"credential file present: {path}")
        lowered = path.name.lower()
        if any(s in lowered for s in FORBIDDEN_SNIPPETS):
            issues.append(f"runtime file present: {path}")
    settings = pkg_root / "settings.yaml"
    if settings.is_file():
        for line in settings.read_text(encoding="utf-8-sig").splitlines():
            if "apiKey:" in line or "secret:" in line:
                issues.append(f"{settings}: inline credential key present")
    return issues


def sha1_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }


def compare(dest_root: Path, manifest: dict, templated: set[str] | None = None) -> tuple[list[str], list[str], list[str]]:
    templated = templated or set()
    dest_files = collect_files(dest_root)
    missing, different, extra = [], [], []
    for rel, digest in manifest.get("digests", {}).items():
        dest = dest_root / rel
        if not dest.is_file():
            missing.append(rel)
        elif rel in templated:
            continue  # content is device-rendered; verify presence only
        elif sha1_file(dest) != digest:
            different.append(rel)
    for rel in dest_files:
        if rel not in manifest.get("digests", {}):
            extra.append(rel)
    return missing, different, extra


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["export", "check", "apply"])
    ap.add_argument("--source", default=None, help="DSH home to export from")
    ap.add_argument("--display", default=None, help="repo skeleton dir (export target / apply package)")
    ap.add_argument("--destination", default=None, help="target DSH home for check/apply")
    ap.add_argument("--name", default="manifest.json")
    ap.add_argument(
        "--template",
        action="store_true",
        help="export: replace known device paths with {{DSH_HOME}}/{{HOME}}/{{DESKTOP}} placeholders",
    )
    ap.add_argument(
        "--with-optional",
        action="store_true",
        help="export: also include optional dirs profiles/, .agent-presets/, patches/",
    )
    args = ap.parse_args()

    src = Path(args.source) if args.source else home_dir()
    display = Path(args.display) if args.display else Path("dsh-config")

    if args.mode == "export":
        if not src.is_dir():
            print(f"FAIL: source is not a directory: {src}")
            return 1
        display.mkdir(parents=True, exist_ok=True)
        digests: dict[str, str] = {}
        include = list(DEFAULT_INCLUDE)
        if args.with_optional:
            include += OPTIONAL_INCLUDE
        for rel in include:
            f = src / rel
            if f.is_file():
                shutil.copy2(f, display / rel)
                digests[rel] = sha1_file(f)
            elif f.is_dir():
                for sub in f.rglob("*"):
                    if not sub.is_file():
                        continue
                    if any(part in HARD_EXCLUDE_DIRS for part in sub.parts):
                        continue
                    rel_sub = (Path(rel) / sub.relative_to(f)).as_posix()
                    target = display / rel_sub
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sub, target)
                    digests[rel_sub] = sha1_file(sub)
        templated: dict[str, dict] = {}
        if args.template:
            for rel in list(digests):
                p = display / rel
                if p.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                text = p.read_text(encoding="utf-8-sig")
                rendered, usage = render_export_text(text)
                if usage:
                    p.write_text(rendered, encoding="utf-8")
                    templated[rel] = usage
                    digests[rel] = sha1_file(p)  # digest of the portable form
        issues = scan_sensitive(display)
        if issues:
            for i in issues:
                print(f"FAIL: {i}")
            return 1
        manifest: dict = {"dsh_home_src": str(src), "digests": digests}
        if templated:
            manifest["templates"] = {"files": templated}
        (display / args.name).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"export: completed -> {display}")
        print(f"sensitive-data scan: PASS")
        print(f"files: {', '.join(digests) or '(none)'}")
        if templated:
            print(f"templated device paths: {', '.join(templated)}")
        return 0

    if not (display / args.name).is_file():
        print(f"FAIL: no manifest at {display / args.name}")
        return 1
    manifest = json.loads((display / args.name).read_text(encoding="utf-8-sig"))
    dest = Path(args.destination) if args.destination else home_dir()
    templated = set((manifest.get("templates") or {}).get("files", {}).keys())

    missing, different, extra = compare(dest, manifest, templated)
    print(f"mode: {args.mode}")
    print(f"missing={missing} different={different} extra={extra}")
    if templated:
        print(f"templated (rendered at apply): {sorted(templated)}")
    if args.mode == "check":
        status = "PASS" if not (missing or different) else "PARTIAL"
        print(f"check: {status}")
        return 0
    if not missing and not different:
        print("apply: nothing to do")
        return 0
    for rel in list(missing) + list(different):
        src_f = display / rel
        if not src_f.is_file():
            continue
        dst_f = dest / rel
        dst_f.parent.mkdir(parents=True, exist_ok=True)
        if rel in templated:
            text = src_f.read_text(encoding="utf-8-sig")
            dst_f.write_text(render_apply_text(text), encoding="utf-8")
        else:
            shutil.copy2(src_f, dst_f)
    missing2, different2, _ = compare(dest, manifest, templated)
    status = "PASS" if not (missing2 or different2) else "FAIL"
    print(f"apply: completed")
    print(f"post-apply SHA-256 check: {status}")
    print(f"destination-only files kept: {len(extra)}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
