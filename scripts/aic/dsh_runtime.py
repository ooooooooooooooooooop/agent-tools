#!/usr/bin/env python3
"""The DSH runtime-composition owner used by aic apply dsh.

This module owns the fixed Node/base/UI/overlay/profile runtime as one
transaction. The existing AIC settings adapter remains responsible for
model/settings projections.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

import yaml


class DshCompositionError(RuntimeError):
    """A deterministic composition failure; live state must remain intact."""


ROOT = Path(__file__).resolve().parents[2]
MANAGED_BEGIN = "# AIC DSH RUNTIME COMPOSITION BEGIN"
MANAGED_END = "# AIC DSH RUNTIME COMPOSITION END"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_portable_file(path: Path) -> str:
    """Hash text assets independent of Git checkout line-ending policy."""
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()),
                       key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _lexists(path: Path) -> bool:
    return os.path.lexists(str(path))


def _remove(path: Path) -> None:
    if not _lexists(path):
        return
    if path.is_dir() and not path.is_symlink():
        # A timed-out package manager may still have raced with directory
        # enumeration. Cleanup must never replace the original composition
        # error with a second missing-file exception.
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _run(cmd: list[str], *, cwd: Path | None = None,
         env: dict[str, str] | None = None, timeout: int = 180) -> str:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                              capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DshCompositionError(f"command failed to start/finish: {cmd}: {exc}") from exc
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise DshCompositionError(f"command failed ({proc.returncode}): {cmd}\n{output[-4000:]}")
    return output


def _git(root: Path, *args: str, timeout: int = 180) -> str:
    return _run(["git", "-C", str(root), *args], timeout=timeout)


def _git_has(root: Path, ref: str) -> bool:
    try:
        _git(root, "cat-file", "-e", f"{ref}^{{commit}}", timeout=20)
        return True
    except DshCompositionError:
        return False


def _config(contract: dict[str, Any]) -> dict[str, Any]:
    cfg = contract.get("runtime_composition")
    if not isinstance(cfg, dict):
        raise DshCompositionError("registry/harnesses/dsh.yaml lacks runtime_composition")
    return cfg


def validate_contract(contract: dict[str, Any], *, check_lock: bool = True) -> list[str]:
    errors: list[str] = []
    try:
        cfg = _config(contract)
        node = cfg["node"]
        base = cfg["base"]
        ui = cfg["ui"]
        profile = cfg["profile"]
        if node["version"] != "v22.19.0":
            errors.append("runtime_composition.node.version must remain v22.19.0")
        if base["package"] != "@deepseek-ai/dsh" or base["version"] != "0.1.1-rc.2":
            errors.append("runtime_composition.base must pin @deepseek-ai/dsh@0.1.1-rc.2")
        patch = ROOT / ui["patch_file"]
        if not patch.is_file():
            errors.append(f"missing UI patch asset: {patch}")
        elif sha256_portable_file(patch) != ui["patch_sha256"]:
            errors.append(f"UI patch SHA-256 mismatch: {patch}")
        build_patch_name = ui.get("build_patch_file")
        build_patch_sha = ui.get("build_patch_sha256")
        if not build_patch_name or not build_patch_sha:
            errors.append("runtime_composition.ui must declare build_patch_file and build_patch_sha256")
        else:
            build_patch = ROOT / build_patch_name
            if not build_patch.is_file():
                errors.append(f"missing UI build patch asset: {build_patch}")
            elif sha256_portable_file(build_patch) != build_patch_sha:
                errors.append(f"UI build patch SHA-256 mismatch: {build_patch}")
        if not profile.get("patch_file") or not profile.get("manifest_file"):
            errors.append("runtime_composition.profile must declare patch_file and manifest_file")
        plugins = cfg["managed_rows"]["plugins"]
        if not plugins or len(plugins) < 5:
            errors.append("runtime_composition must declare at least five managed plugins")
        ids = [p.get("id") for p in plugins]
        if len(set(ids)) != len(ids):
            errors.append("runtime_composition plugin ids must be unique")
        for plugin in plugins:
            for key in ("package", "version", "source_relative", "plugin_directory", "entry_relative"):
                if not plugin.get(key):
                    errors.append(f"plugin {plugin.get('id')} missing {key}")
            source_entry = ROOT / plugin["source_relative"] / plugin["entry_relative"]
            package_json = ROOT / plugin["source_relative"] / "package.json"
            if not source_entry.is_file():
                errors.append(f"missing overlay entry: {source_entry}")
            if not package_json.is_file():
                errors.append(f"missing overlay package manifest: {package_json}")
        anchor = cfg.get("archive_anchor", {})
        if not re.fullmatch(r"[0-9a-f]{64}", str(anchor.get("artifact_sha256", ""))):
            errors.append("archive_anchor.artifact_sha256 must be a lowercase SHA-256")
        lock_path = ROOT / "registry" / "runtime.lock.yaml"
        if lock_path.is_file() and check_lock:
            lock = yaml.safe_load(lock_path.read_text(encoding="utf-8-sig")) or {}
            runtime_lock = lock.get("runtime_lock", {})
            dsh_lock = runtime_lock.get("dsh", {})
            if dsh_lock.get("package") != base["package"] or dsh_lock.get("version") != base["version"]:
                errors.append("runtime.lock.yaml DSH package/version disagrees with runtime_composition")
            if dsh_lock.get("install_mode") != base["install_mode"]:
                errors.append("runtime.lock.yaml DSH install_mode must be managed-profile-fixed-directory")
            if runtime_lock.get("node") != node["version"]:
                errors.append("runtime.lock.yaml Node version disagrees with runtime_composition")
            if runtime_lock.get("node_relative_path") != node["relative_to_dsh_home"] + "/node.exe":
                errors.append("runtime.lock.yaml Node path disagrees with runtime_composition")
    except (KeyError, TypeError) as exc:
        errors.append(f"runtime_composition schema error: {exc}")
    return errors


def _powershell_launcher(cfg: dict[str, Any]) -> str:
    return r'''param(
  [string]$ProfileRoot = $PSScriptRoot,
  [string]$NodePath = $env:DSH_NODE_PATH
)

$ErrorActionPreference = 'Stop'
$DshHome = Split-Path -Parent (Split-Path -Parent $ProfileRoot)
$statePath = Join-Path $ProfileRoot 'dsh-managed-state.json'
$manifestPath = Join-Path $ProfileRoot 'dsh-runtime-composition.json'

# Resolve the accepted managed composition from durable state, falling back to
# the composition manifest. No version string is hardcoded here.
$nodeRel = $null
$baseVersion = $null
$entryRel = 'node_modules\@deepseek-ai\dsh\lib\bin.js'
if (Test-Path -LiteralPath $statePath) {
  $st = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
  $nodeRel = $st.current.nodeRelativePath
  $baseVersion = $st.current.version
  if ($st.current.entryRelative) { $entryRel = $st.current.entryRelative }
} elseif (Test-Path -LiteralPath $manifestPath) {
  $m = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  $nodeRel = $m.node.relativePath
  $baseVersion = $m.base.version
  $entryRel = $m.base.entryRelative
}
if ([string]::IsNullOrWhiteSpace($nodeRel) -or [string]::IsNullOrWhiteSpace($baseVersion)) {
  throw 'Managed composition state unavailable (dsh-managed-state.json / dsh-runtime-composition.json)'
}
$distributionRoot = Join-Path $ProfileRoot "base-dsh-$baseVersion"
$managedNodePath = Join-Path $DshHome ($nodeRel -replace '/', '\\')
$managedNodePath = Join-Path $managedNodePath 'node.exe'
if ([string]::IsNullOrWhiteSpace($NodePath)) { $NodePath = $managedNodePath }
$entry = Join-Path $DshHome ($entryRel -replace '/', '\')
$packageJson = Join-Path $distributionRoot 'node_modules\@deepseek-ai\dsh\package.json'

if (!(Test-Path -LiteralPath $NodePath)) { throw "Managed Node runtime not found: $NodePath" }
if (!(Test-Path -LiteralPath $entry) -or !(Test-Path -LiteralPath $packageJson)) { throw "Pinned DSH distribution is incomplete: $distributionRoot" }
$nodeVersion = (& $NodePath --version).Trim()
if ($nodeVersion -notmatch '^v(22\.(?:19|2[0-9])|(?:2[4-9]|[3-9][0-9])\.)') { throw "Unsupported Node runtime $nodeVersion; require >=22.19.0 or >=24." }
$package = Get-Content -LiteralPath $packageJson -Raw | ConvertFrom-Json
if ($package.name -ne '@deepseek-ai/dsh' -or $package.version -ne $baseVersion) { throw "Pinned DSH package mismatch: $($package.name)@$($package.version) (expected @deepseek-ai/dsh@$baseVersion)" }

Set-Location -LiteralPath $ProfileRoot
& $NodePath $entry web --no-open
exit $LASTEXITCODE
'''


def _managed_rows(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(cfg["managed_rows"]["disable"])
    for plugin in cfg["managed_rows"]["plugins"]:
        row: dict[str, Any] = {
            "id": plugin["id"],
            "name": f"./plugins/{plugin['plugin_directory']}/{plugin['entry_relative']}".replace("\\", "/"),
        }
        if plugin.get("config"):
            row["config"] = plugin["config"]
        rows.append(row)
    return rows


def _strip_legacy_blocks(text: str, known_ids: set[str]) -> str:
    text = re.sub(
        rf"(?ms)^" + re.escape(MANAGED_BEGIN) + r"\n.*?^" + re.escape(MANAGED_END) + r"\s*\n?",
        "",
        text,
    )
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if re.match(r"^-\s", line)]
    if not starts:
        return text
    starts.append(len(lines))
    kept: list[str] = []
    for left, right in zip(starts, starts[1:]):
        block = "".join(lines[left:right])
        ids = set(re.findall(r"^\s*-\s+id:\s*([^\s#]+)", block, flags=re.M))
        if ids & known_ids:
            unknown = ids - known_ids
            if unknown:
                raise DshCompositionError(
                    "legacy DSH managed block mixes unknown rows; refusing to rewrite: "
                    + ", ".join(sorted(unknown))
                )
            continue
        kept.append(block)
    prefix = "".join(lines[:starts[0]]) if starts else text
    return prefix + "".join(kept)


def render_patch(existing: Path | None, cfg: dict[str, Any]) -> tuple[str, str]:
    old = existing.read_text(encoding="utf-8-sig") if existing and existing.is_file() else ""
    known_ids = {r["id"] for r in _managed_rows(cfg)}
    base = _strip_legacy_blocks(old, known_ids).rstrip()
    rows = _managed_rows(cfg)
    disable_ids = {r["id"] for r in cfg["managed_rows"]["disable"]}
    managed_rows = [r for r in rows if r["id"] in disable_ids]
    managed_rows.append({"insert": [r for r in rows if r["id"] not in disable_ids]})
    managed = yaml.safe_dump(managed_rows, allow_unicode=True, sort_keys=False)
    block = f"{MANAGED_BEGIN}\n{managed}{MANAGED_END}\n"
    return ((base + "\n\n" if base else "") + block, sha256_text(block))


def _managed_block(text: str) -> str | None:
    match = re.search(
        rf"(?ms)^{re.escape(MANAGED_BEGIN)}\n.*?^{re.escape(MANAGED_END)}\n?",
        text,
    )
    return match.group(0) if match else None


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root):
                raise DshCompositionError(f"unsafe Node archive member: {member.filename}")
        zf.extractall(destination)


def _copy_directory(source: Path, destination: Path) -> None:
    """Copy a test/bootstrap source tree without following transient links.

    The normal path installs the pinned package from the registry. The
    DSH_BASE_SOURCE escape hatch is only for offline fixtures and snapshots;
    on Windows robocopy is used because npm's generated dependency tree can
    contain paths for which shutil.copytree races with directory enumeration.
    """
    if os.name == "nt" and shutil.which("robocopy"):
        destination.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["robocopy", str(source), str(destination), "/E", "/COPY:DAT",
             "/DCOPY:DAT", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1800,
        )
        if proc.returncode > 7:
            raise DshCompositionError(
                f"offline base snapshot copy failed ({proc.returncode}): "
                f"{(proc.stdout + proc.stderr)[-2000:]}"
            )
        return
    shutil.copytree(source, destination)


def _node_runtime(stage: Path, home: Path, cfg: dict[str, Any]) -> tuple[Path, str, str]:
    node_cfg = cfg["node"]
    version = node_cfg["version"]
    dirname = f"node-{version}-win-x64"
    target = stage / "runtime" / dirname
    source_env = os.environ.get("DSH_NODE_SOURCE")
    if source_env:
        source = Path(source_env)
        source = source.parent if source.is_file() else source
        if not (source / "node.exe").is_file():
            raise DshCompositionError(f"DSH_NODE_SOURCE does not contain node.exe: {source}")
        shutil.copytree(source, target)
    else:
        cache = home / "cache" / "node"
        cache.mkdir(parents=True, exist_ok=True)
        archive_cfg = node_cfg["archive"]["windows-x64"]
        archive = cache / f"{dirname}.zip"
        if not archive.is_file() or sha256_file(archive) != archive_cfg["sha256"]:
            tmp = archive.with_suffix(".download")
            try:
                urllib.request.urlretrieve(archive_cfg["url"], tmp)
                if sha256_file(tmp) != archive_cfg["sha256"]:
                    raise DshCompositionError("downloaded Node archive SHA-256 mismatch")
                os.replace(tmp, archive)
            finally:
                tmp.unlink(missing_ok=True)
        _safe_extract_zip(archive, stage / "runtime")
        extracted = stage / "runtime" / dirname
        if extracted != target and extracted.is_dir():
            os.replace(extracted, target)
    node_exe = target / "node.exe"
    actual = _run([str(node_exe), "--version"], timeout=20).splitlines()[-1].strip()
    if actual != version:
        raise DshCompositionError(f"Node version mismatch: expected {version}, got {actual}")
    return target, actual, sha256_file(node_exe)


def _install_base(stage_profile: Path, node_root: Path, cfg: dict[str, Any], home: Path | None = None) -> Path:
    base_cfg = cfg["base"]
    base_root = stage_profile / f"base-dsh-{base_cfg['version']}"
    source_env = os.environ.get("DSH_BASE_SOURCE")
    if not source_env and home:
        live_base = home / cfg["profile"]["relative_to_dsh_home"] / f"base-dsh-{base_cfg['version']}"
        cand_pkg = live_base / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
        cand_entry = live_base / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
        if cand_pkg.is_file() and cand_entry.is_file():
            try:
                pkg_data = json.loads(cand_pkg.read_text(encoding="utf-8-sig"))
                if pkg_data.get("name") == base_cfg["package"] and pkg_data.get("version") == base_cfg["version"]:
                    source_env = str(live_base)
            except Exception:
                pass
    if source_env:
        source = Path(source_env)
        package_json = source / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
        if not package_json.is_file():
            raise DshCompositionError(f"DSH_BASE_SOURCE is not an installed base: {source}")
        package = json.loads(package_json.read_text(encoding="utf-8-sig"))
        if package.get("name") != base_cfg["package"] or package.get("version") != base_cfg["version"]:
            raise DshCompositionError("DSH_BASE_SOURCE package identity mismatch")
        _copy_directory(source, base_root)
    else:
        node_exe = node_root / "node.exe"
        npm_cli = node_root / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if not node_exe.is_file() or not npm_cli.is_file():
            raise DshCompositionError(
                f"managed Node distribution lacks bundled npm CLI: {npm_cli}"
            )
        env = {**os.environ, "PATH": str(node_root) + os.pathsep + os.environ.get("PATH", "")}
        install_args = [
            str(node_exe), str(npm_cli), "install", "--prefix", str(base_root),
            "--ignore-scripts", "--no-audit", "--no-fund", "--package-lock=false",
            "--omit=dev", "--install-strategy=shallow", "--prefer-offline",
            "--progress=false", "--legacy-peer-deps",
            f"{base_cfg['package']}@{base_cfg['version']}",
        ]
        _run(install_args, env=env, timeout=900)
        for _ in range(4):
            peer_specs = _missing_peer_specs(base_root)
            if not peer_specs:
                break
            _run([
                str(node_exe), str(npm_cli), "install", "--prefix", str(base_root),
                "--ignore-scripts", "--no-audit", "--no-fund", "--package-lock=false",
                "--no-save", "--omit=dev", "--install-strategy=shallow", "--prefer-offline",
                "--progress=false", "--legacy-peer-deps", *peer_specs,
            ], env=env, timeout=900)
        else:
            raise DshCompositionError("DSH base peer dependency closure did not converge")
    entry = base_root / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    package_json = base_root / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
    if not entry.is_file() or not package_json.is_file():
        raise DshCompositionError("installed DSH base is incomplete")
    package = json.loads(package_json.read_text(encoding="utf-8-sig"))
    if package.get("name") != base_cfg["package"] or package.get("version") != base_cfg["version"]:
        raise DshCompositionError("installed DSH base package identity mismatch")
    node_exe = node_root / "node.exe"
    try:
        _run([str(node_exe), str(entry), "--help"], cwd=base_root, timeout=60)
    except DshCompositionError as exc:
        raise DshCompositionError(f"installed DSH base failed startup dependency check: {exc}") from exc
    return base_root


def _resolvable_package_json(start: Path, package_name: str) -> bool:
    """Apply Node's upward node_modules lookup for a package name."""
    relative = Path(*package_name.split("/"))
    current = start
    for parent in (current, *current.parents):
        if (parent / "node_modules" / relative / "package.json").is_file():
            return True
    return False


def _missing_peer_specs(base_root: Path) -> list[str]:
    """Return required, registry-resolvable peer packages absent from the staged Base."""
    missing: dict[str, str] = {}
    modules = base_root / "node_modules"
    if not modules.is_dir():
        return []
    for package_json in modules.rglob("package.json"):
        try:
            package = json.loads(package_json.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        peers = package.get("peerDependencies") or {}
        optional = (package.get("peerDependenciesMeta") or {})
        for name, requirement in peers.items():
            if str(requirement).startswith("workspace:"):
                continue
            if optional.get(name, {}).get("optional"):
                continue
            if not _resolvable_package_json(package_json.parent, name):
                missing.setdefault(name, f"{name}@{requirement}")
    return sorted(missing.values())


def _resolve_harness_root(home: Path, ui_cfg: dict[str, Any]) -> tuple[Path, str, Any]:
    configured = os.environ.get(ui_cfg["source_root_env"])
    cache = home / "cache" / "sources"
    cache.mkdir(parents=True, exist_ok=True)
    source = Path(configured) if configured else cache / "deepseek-harness"
    if not (source / ".git").exists():
        if configured:
            raise DshCompositionError(f"{ui_cfg['source_root_env']} is not a git checkout: {source}")
        _run(["git", "clone", "--no-checkout", ui_cfg["repository"], str(source)], timeout=900)
    baseline = ui_cfg["baseline_commit"]
    fix = ui_cfg["fix_commit"]
    patch = ROOT / ui_cfg["patch_file"]
    apply_patch = bool(ui_cfg.get("apply_patch", True))
    fix_is_recoverable = ui_cfg.get("fix_commit_remote") not in (None, "", "unavailable-at-audit")
    if _git_has(source, fix) and fix_is_recoverable and apply_patch:
        # Always build from a clean detached worktree. The configured checkout
        # may carry unrelated user changes (including staged build tooling);
        # those must never leak into the released UI bundle.
        ref = fix
        source_state = fix
    else:
        if not _git_has(source, baseline):
            _run(["git", "fetch", "--depth", "1", "origin", baseline], cwd=source, timeout=900)
        ref = baseline
        source_state = str(baseline)
        if apply_patch:
            source_state = f"{baseline}+patch:{ui_cfg['patch_sha256']}"
    worktree = Path(tempfile.mkdtemp(prefix="dsh-ui-", dir=str(cache)))
    _remove(worktree)
    _run(["git", "worktree", "add", "--detach", str(worktree), ref], cwd=source, timeout=180)
    if ref != fix and apply_patch:
        _run(["git", "apply", "--check", str(patch)], cwd=worktree, timeout=30)
        _run(["git", "apply", str(patch)], cwd=worktree, timeout=30)
    build_patch_name = ui_cfg.get("build_patch_file")
    if build_patch_name and apply_patch:
        build_patch = ROOT / build_patch_name
        _run(["git", "apply", "--check", str(build_patch)], cwd=worktree, timeout=30)
        _run(["git", "apply", str(build_patch)], cwd=worktree, timeout=30)
        source_state = f"{source_state}+build-patch:{ui_cfg['build_patch_sha256']}"

    def cleanup() -> None:
        try:
            _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=source, timeout=60)
        finally:
            _remove(worktree)

    return worktree, source_state, cleanup


_SOURCE_STATE_RE = re.compile(
    r"^(?P<commit>[0-9a-f]{40})"
    r"(?P<suffix>(?:\+(?:patch|build-patch):[0-9a-f]{64})*)$"
)


def _expected_ui_source_states(ui_cfg: dict[str, Any]) -> list[str]:
    """Return source provenance strings that the current contract can build."""
    baseline = str(ui_cfg["baseline_commit"])
    if not ui_cfg.get("apply_patch", True):
        return [baseline]
    patched = f"{baseline}+patch:{ui_cfg['patch_sha256']}"
    if ui_cfg.get("build_patch_sha256"):
        patched += f"+build-patch:{ui_cfg['build_patch_sha256']}"
    states = [patched]
    if ui_cfg.get("fix_commit_remote") not in (None, "", "unavailable-at-audit"):
        # A recoverable fix commit is a second valid build input. Which one is
        # selected is determined by the source checkout's available objects.
        fix_state = str(ui_cfg["fix_commit"])
        if ui_cfg.get("build_patch_sha256"):
            fix_state += f"+build-patch:{ui_cfg['build_patch_sha256']}"
        states.insert(0, fix_state)
    return states


def _parse_ui_source_state(source_state: str) -> dict[str, str] | None:
    match = _SOURCE_STATE_RE.fullmatch(source_state)
    if not match:
        return None
    return {"commit": match.group("commit"), "suffix": match.group("suffix")}


def _classify_ui_source_state(manifest_ui: dict[str, Any],
                              ui_cfg: dict[str, Any]) -> dict[str, Any]:
    """Classify build provenance independently from artifact/runtime drift.

    `sourceState` is an immutable record of the effective UI build input. A
    changed canonical baseline therefore requires reconciliation, but is not
    the same thing as a generated/runtime artifact mismatch.
    """
    actual = str(manifest_ui.get("sourceState", "") or "")
    expected = _expected_ui_source_states(ui_cfg)
    parsed = _parse_ui_source_state(actual)
    recorded_baseline = str(manifest_ui.get("baselineCommit", "") or "")
    if (parsed is not None and recorded_baseline and
            recorded_baseline != parsed["commit"]):
        kind = "SOURCE_CONTRACT_GAP"
    elif actual in expected:
        kind = "CURRENT"
    elif parsed is None:
        kind = "SOURCE_CONTRACT_GAP"
    elif (recorded_baseline == parsed["commit"] and
          recorded_baseline != str(ui_cfg["baseline_commit"])):
        # The canonical baseline is current; the deployed manifest is the
        # stale object. Keep that distinction explicit for incident triage.
        kind = "STALE_DEPLOYED_RECIPE"
    elif parsed["commit"] != str(ui_cfg["baseline_commit"]):
        kind = "SOURCE_ADVANCED"
    else:
        # The commit is current but its transformation suffix is not one the
        # contract can reproduce. This is provenance corruption, not advance.
        kind = "SOURCE_CONTRACT_GAP"
    return {
        "kind": kind,
        "expected": expected[0] if len(expected) == 1 else expected,
        "actual": actual or "missing",
        "sourceCommit": parsed["commit"] if parsed else None,
        "reconciliationRequired": kind != "CURRENT",
    }


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _classify_ui_source_checkout(source: Path, ui_cfg: dict[str, Any]) -> dict[str, Any]:
    """Describe checkout movement without treating dirty work as deployment drift."""
    head = _git(source, "rev-parse", "HEAD", timeout=20).strip()
    dirty = bool(_git(source, "status", "--porcelain=v1", "--untracked-files=all", timeout=20).strip())
    baseline = str(ui_cfg["baseline_commit"])
    accepted = {baseline}
    if (ui_cfg.get("apply_patch", True) and
            ui_cfg.get("fix_commit_remote") not in (None, "", "unavailable-at-audit")):
        accepted.add(str(ui_cfg["fix_commit"]))
    if head in accepted:
        relation = "CURRENT"
    elif any(_git_is_ancestor(source, ref, head) for ref in accepted):
        relation = "SOURCE_ADVANCED"
    elif any(_git_is_ancestor(source, head, ref) for ref in accepted):
        relation = "SOURCE_ROLLBACK"
    else:
        relation = "SOURCE_DIVERGED"
    return {
        "kind": relation,
        "head": head,
        "dirty": dirty,
        "dirtyState": "DIRTY_KNOWN" if dirty else "CLEAN",
        "acceptedRefs": sorted(accepted),
        "reconciliationRequired": relation in {"SOURCE_ROLLBACK", "SOURCE_DIVERGED"},
    }


def _verify_ui_source_state(source: Path, ui_cfg: dict[str, Any]) -> dict[str, Any]:
    """Return checkout provenance; dirty work is safe because builds detach."""
    report = _classify_ui_source_checkout(source, ui_cfg)
    if report["kind"] in {"SOURCE_ROLLBACK", "SOURCE_DIVERGED"}:
        raise DshCompositionError(
            f"UI source checkout {report['kind'].lower()}: HEAD={report['head']}"
        )
    return report


def _pnpm_command() -> str:
    for name in ("pnpm.CMD", "pnpm.cmd", "pnpm"):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    raise DshCompositionError("pnpm is required to build the fixed Harness UI")


def _validate_ui_version_alignment(source: Path, cfg: dict[str, Any]) -> None:
    """Reject a UI release whose package contracts do not match the pinned base."""
    ui = cfg["ui"]
    expected_version = str(cfg["base"]["version"])
    package_specs = [
        ("client", ui["client_package"], Path(ui["client_bundle_relative"]).parent.parent / "package.json"),
        ("web", ui["web_package"], Path(ui["web_dist_relative"]).parent / "package.json"),
    ]
    for label, expected_name, relative_manifest in package_specs:
        manifest_path = source / relative_manifest
        if not manifest_path.is_file():
            raise DshCompositionError(
                f"UI {label} package manifest is missing: {manifest_path}"
            )
        try:
            package = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DshCompositionError(
                f"UI {label} package manifest is invalid: {manifest_path}: {exc}"
            ) from exc
        actual_name = package.get("name")
        actual_version = package.get("version")
        if actual_name != expected_name or actual_version != expected_version:
            raise DshCompositionError(
                "UI/base package mismatch: "
                f"{label} expected {expected_name}@{expected_version}, "
                f"got {actual_name}@{actual_version} ({manifest_path})"
            )


def _build_ui(source: Path, node_root: Path, cfg: dict[str, Any]) -> tuple[Path, Path, str]:
    ui = cfg["ui"]
    _validate_ui_version_alignment(source, cfg)
    env = {**os.environ, "PATH": str(node_root) + os.pathsep + os.environ.get("PATH", "")}
    pnpm = _pnpm_command()
    if not (source / "node_modules").is_dir():
        _run([pnpm, "install", "--frozen-lockfile"], cwd=source, env=env, timeout=1800)
    # The UI package's bundle config consumes the generated lib/types entries;
    # a clean detached worktree has no ignored build output, so materialize the
    # normal Harness library build before the package and web builds.
    _run([pnpm, "run", "build:lib"], cwd=source, env=env, timeout=1800)
    _run([pnpm, "--filter", ui["client_package"], "bundle"], cwd=source, env=env, timeout=900)
    _run([pnpm, "--filter", ui["web_package"], "build"], cwd=source, env=env, timeout=900)
    client = source / ui["client_bundle_relative"]
    web_dist = source / ui["web_dist_relative"]
    if not client.is_file() or not web_dist.is_dir():
        raise DshCompositionError("Harness UI build did not produce client bundle and web dist")
    return client, web_dist, sha256_tree(web_dist)


def _dsh_resolved_dependency_root(base_root: Path, package_name: str) -> Path:
    relative = Path(*package_name.split("/"))
    nested = base_root / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" / relative
    root = base_root / "node_modules" / relative
    return nested if (nested / "package.json").is_file() else root


def _copy_ui(base_root: Path, client: Path, web_dist: Path) -> tuple[Path, Path, str]:
    client_roots = [
        base_root / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" /
        "@deepseek-ai" / "dsh-client-ui-conversation",
        base_root / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation",
    ]
    for package_root in client_roots:
        package_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(client.parent.parent / "package.json", package_root / "package.json")
        _remove(package_root / "lib")
        shutil.copytree(client.parent, package_root / "lib")
    client_dest = _dsh_resolved_dependency_root(base_root, "@deepseek-ai/dsh-client-ui-conversation") / "lib" / "client.js"
    if sha256_file(client_dest) != sha256_file(client):
        raise DshCompositionError("UI client bundle copy hash mismatch")
    frontend_roots = [
        base_root / "node_modules" / "@deepseek-ai" / "dsh" / "node_modules" /
        "@deepseek-ai" / "dsh-web-frontend",
        base_root / "node_modules" / "@deepseek-ai" / "dsh-web-frontend",
    ]
    source_package = web_dist.parent / "package.json"
    for frontend in frontend_roots:
        frontend.mkdir(parents=True, exist_ok=True)
        if source_package.is_file():
            shutil.copy2(source_package, frontend / "package.json")
        _remove(frontend / "dist")
        shutil.copytree(web_dist, frontend / "dist")
    frontend_dest = _dsh_resolved_dependency_root(base_root, "@deepseek-ai/dsh-web-frontend") / "dist"
    return client_dest, frontend_dest, sha256_tree(frontend_dest)


def _copy_overlays(stage_profile: Path, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for order, plugin in enumerate(cfg["managed_rows"]["plugins"], start=1):
        source_root = ROOT / plugin["source_relative"]
        source_entry = source_root / plugin["entry_relative"]
        destination = stage_profile / "plugins" / plugin["plugin_directory"]
        deployed_entry = destination / plugin["entry_relative"]
        deployed_entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_entry, deployed_entry)
        shutil.copy2(source_root / "package.json", destination / "package.json")
        source_package = json.loads((source_root / "package.json").read_text(encoding="utf-8"))
        if source_package.get("name") != plugin["package"] or source_package.get("version") != plugin["version"]:
            raise DshCompositionError(f"overlay package identity mismatch: {plugin['id']}")
        record = {
            "id": plugin["id"],
            "package": plugin["package"],
            "version": plugin["version"],
            "loadOrder": order,
            "sourceRelative": str(Path(plugin["source_relative"]) / plugin["entry_relative"]).replace("\\", "/"),
            "sourceSha256": sha256_file(source_entry),
            "deploymentRelative": str(Path(cfg["profile"]["relative_to_dsh_home"]) / "plugins" /
                                        plugin["plugin_directory"] / plugin["entry_relative"]).replace("\\", "/"),
            "deploymentSha256": sha256_file(deployed_entry),
        }
        if plugin["id"] == "compaction-basic-convergence":
            marker = {
                "schema_version": 1,
                "deployed_version": plugin["version"],
                "fork_lib_index_sha256": record["deploymentSha256"],
            }
            marker_path = destination / "lib" / ".dsh-convergence.json"
            marker_path.write_text(json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8")
            record["markerSha256"] = sha256_file(marker_path)
        result.append(record)
    return result


def _stable_manifest(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {**payload, "profileCombinationHash": value}, value


def _publish(home: Path, entries: list[tuple[str, Path]]) -> str:
    txid = f"dsh-{uuid.uuid4().hex}"
    backup = home / ".aic-dsh-backups" / txid
    backup.mkdir(parents=True, exist_ok=True)
    tx_entries: list[dict[str, Any]] = []
    published: list[dict[str, Any]] = []
    fail_after = int(os.environ.get("AIC_DSH_FAIL_AFTER", "0") or 0)
    try:
        for index, (relative, staged) in enumerate(entries, start=1):
            live = home / relative
            backup_path = backup / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            had_original = _lexists(live)
            record = {"live": relative, "backup": str(backup_path.relative_to(home)).replace("\\", "/"),
                      "hadOriginal": had_original}
            if had_original:
                os.replace(live, backup_path)
            tx_entries.append(record)
            published.append(record)
            live.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, live)
            if fail_after and index >= fail_after:
                raise DshCompositionError(f"injected failure after publish {index}")
        (backup / "transaction.json").write_text(json.dumps({
            "transactionId": txid, "status": "COMMITTED", "entries": tx_entries,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return txid
    except Exception:
        for record in reversed(published):
            live = home / record["live"]
            _remove(live)
            if record["hadOriginal"]:
                old = home / record["backup"]
                if _lexists(old):
                    live.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old, live)
        raise


def _find_archive(home: Path, session_id: str) -> Path | None:
    sessions = home / "sessions"
    if not sessions.is_dir():
        return None
    files = [p for p in sorted(sessions.rglob(f"*{session_id}*")) if p.is_file()]
    return files[0] if files else None


def inspect(home: Path, contract: dict[str, Any]) -> dict[str, Any]:
    cfg = _config(contract)
    profile = home / cfg["profile"]["relative_to_dsh_home"]
    manifest_path = profile / cfg["profile"]["manifest_file"]
    findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    source_state_report: dict[str, Any] | None = None
    source_checkout_report: dict[str, Any] | None = None

    def finding(category: str, component: str, expected: Any, actual: Any) -> None:
        findings.append({"category": category, "component": component,
                         "expected": expected, "actual": actual})

    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            finding("CONFIG_DRIFT", "composition-manifest", "valid JSON", str(exc))
    else:
        finding("CONFIG_DRIFT", "composition-manifest", "present", "missing")

    node_cfg = cfg["node"]
    node_exe = home / node_cfg["relative_to_dsh_home"] / "node.exe"
    actual_node = "missing"
    if node_exe.is_file():
        try:
            actual_node = _run([str(node_exe), "--version"], timeout=20).splitlines()[-1].strip()
        except DshCompositionError as exc:
            actual_node = str(exc)
    if actual_node != node_cfg["version"]:
        finding("RUNTIME_DRIFT", "node.version", node_cfg["version"], actual_node)
    if manifest.get("node", {}).get("sha256") and node_exe.is_file():
        actual_hash = sha256_file(node_exe)
        if actual_hash != manifest["node"]["sha256"]:
            finding("DEPLOYMENT_DRIFT", "node.exe", manifest["node"]["sha256"], actual_hash)

    base = cfg["base"]
    base_root = profile / f"base-dsh-{base['version']}"
    base_entry = base_root / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    base_pkg = base_root / "node_modules" / "@deepseek-ai" / "dsh" / "package.json"
    actual_base = "missing"
    if base_pkg.is_file():
        actual_base = json.loads(base_pkg.read_text(encoding="utf-8-sig")).get("version")
    if actual_base != base["version"]:
        finding("RUNTIME_DRIFT", "base.version", base["version"], actual_base)
    if not base_entry.is_file():
        finding("DEPLOYMENT_DRIFT", "base.entry", "present", "missing")
    elif manifest.get("base", {}).get("entrySha256"):
        actual_hash = sha256_file(base_entry)
        if actual_hash != manifest["base"]["entrySha256"]:
            finding("DEPLOYMENT_DRIFT", "base.entrySha256", manifest["base"]["entrySha256"], actual_hash)
    if node_exe.is_file() and base_entry.is_file():
        try:
            _run([str(node_exe), str(base_entry), "--help"], cwd=base_root, timeout=60)
        except DshCompositionError as exc:
            finding("RUNTIME_DRIFT", "base.startup-dependencies", "DSH CLI help exits 0", str(exc))

    client = _dsh_resolved_dependency_root(base_root, "@deepseek-ai/dsh-client-ui-conversation") / "lib" / "client.js"
    if not client.is_file():
        finding("DEPLOYMENT_DRIFT", "ui.client.bundle", "present", "missing")
    elif manifest.get("ui", {}).get("clientBundleSha256"):
        actual_hash = sha256_file(client)
        if actual_hash != manifest["ui"]["clientBundleSha256"]:
            finding("DEPLOYMENT_DRIFT", "ui.client.bundle", manifest["ui"]["clientBundleSha256"], actual_hash)
    frontend = _dsh_resolved_dependency_root(base_root, "@deepseek-ai/dsh-web-frontend") / "dist"
    if not frontend.is_dir():
        finding("DEPLOYMENT_DRIFT", "ui.web.dist", "present", "missing")
    elif manifest.get("ui", {}).get("webDistSha256"):
        actual_hash = sha256_tree(frontend)
        if actual_hash != manifest["ui"]["webDistSha256"]:
            finding("DEPLOYMENT_DRIFT", "ui.web.dist", manifest["ui"]["webDistSha256"], actual_hash)

    patch_path = profile / cfg["profile"]["patch_file"]
    _, managed_hash = render_patch(None, cfg)
    if not patch_path.is_file():
        finding("CONFIG_DRIFT", "cordis.patch.yml", "present", "missing")
    else:
        actual_patch = patch_path.read_text(encoding="utf-8-sig")
        if MANAGED_BEGIN not in actual_patch or MANAGED_END not in actual_patch:
            finding("CONFIG_DRIFT", "cordis.patch.yml.managed-block", "present", "missing")
        else:
            actual_block = _managed_block(actual_patch)
            actual_block_hash = sha256_text(actual_block) if actual_block is not None else "missing"
            if actual_block_hash != managed_hash:
                finding("GENERATED_DRIFT", "cordis.patch.yml.managed-block",
                        managed_hash, actual_block_hash)
            elif managed_hash != str(manifest.get("cordisPatch", {}).get("managedBlockSha256", "")):
                finding("CONFIG_DRIFT", "cordis.patch.yml.managed-block", managed_hash,
                        manifest.get("cordisPatch", {}).get("managedBlockSha256", "missing"))

    launcher = profile / cfg["profile"]["launcher_file"]
    if not launcher.is_file():
        finding("DEPLOYMENT_DRIFT", "launcher", "present", "missing")
    else:
        actual_hash = sha256_file(launcher)
        if manifest.get("launcher", {}).get("sha256") and actual_hash != manifest["launcher"]["sha256"]:
            finding("DEPLOYMENT_DRIFT", "launcher", manifest["launcher"]["sha256"], actual_hash)
        generated_hash = sha256_text(_powershell_launcher(cfg))
        actual_portable_hash = sha256_portable_file(launcher)
        if actual_portable_hash != generated_hash:
            finding("CONFIG_DRIFT", "launcher.generated", generated_hash, actual_portable_hash)

    expected = {p["id"]: p for p in cfg["managed_rows"]["plugins"]}
    deployed = {p["id"]: p for p in manifest.get("overlays", [])}
    for order, plugin in enumerate(cfg["managed_rows"]["plugins"], start=1):
        source_entry = ROOT / plugin["source_relative"] / plugin["entry_relative"]
        dest = profile / "plugins" / plugin["plugin_directory"] / plugin["entry_relative"]
        source_hash = sha256_file(source_entry) if source_entry.is_file() else "missing"
        if deployed.get(plugin["id"], {}).get("sourceSha256") != source_hash:
            finding("SOURCE_DRIFT", plugin["id"], source_hash,
                    deployed.get(plugin["id"], {}).get("sourceSha256", "missing"))
        if not dest.is_file():
            finding("DEPLOYMENT_DRIFT", plugin["id"], "present", "missing")
        elif sha256_file(dest) != source_hash:
            finding("DEPLOYMENT_DRIFT", plugin["id"], source_hash, sha256_file(dest))
        if plugin["id"] == "compaction-basic-convergence":
            marker_path = dest.parent / ".dsh-convergence.json"
            expected_marker = deployed.get(plugin["id"], {}).get("markerSha256")
            if not marker_path.is_file():
                finding("DEPLOYMENT_DRIFT", plugin["id"] + ".marker", "present", "missing")
            elif expected_marker and sha256_file(marker_path) != expected_marker:
                finding("DEPLOYMENT_DRIFT", plugin["id"] + ".marker", expected_marker, sha256_file(marker_path))
        if deployed.get(plugin["id"], {}).get("loadOrder") != order:
            finding("CONFIG_DRIFT", f"{plugin['id']}.loadOrder", order,
                    deployed.get(plugin["id"], {}).get("loadOrder", "missing"))

    for extra_id in sorted(set(deployed.keys()) - set(expected.keys())):
        finding("CONFIG_DRIFT", f"extra-overlay:{extra_id}", "absent", "deployed")

    plugins_dir = profile / "plugins"
    if plugins_dir.is_dir():
        expected_dirs = {p["plugin_directory"] for p in cfg["managed_rows"]["plugins"]}
        for d in sorted(plugins_dir.iterdir()):
            if d.is_dir() and d.name not in expected_dirs:
                finding("DEPLOYMENT_DRIFT", f"unmanaged-plugin-dir:{d.name}", "absent", "present")

    anchor = cfg.get("archive_anchor", {})
    archive = _find_archive(home, anchor.get("session_id", ""))
    if archive is None:
        warnings.append({"category": "RUNTIME_DRIFT", "component": "archived-session",
                         "expected": anchor.get("artifact_sha256"), "actual": "not-present-on-this-profile"})
    elif sha256_file(archive) != anchor.get("artifact_sha256"):
        finding("RUNTIME_DRIFT", "archived-session", anchor.get("artifact_sha256"), sha256_file(archive))

    if manifest:
        ui_cfg = cfg["ui"]
        configured_source = os.environ.get(ui_cfg["source_root_env"])
        if configured_source and (Path(configured_source) / ".git").exists():
            try:
                source_checkout_report = _classify_ui_source_checkout(Path(configured_source), ui_cfg)
                if source_checkout_report["kind"] in {"SOURCE_ROLLBACK", "SOURCE_DIVERGED"}:
                    finding(source_checkout_report["kind"], "ui.source-checkout",
                            source_checkout_report["acceptedRefs"], source_checkout_report["head"])
            except DshCompositionError as exc:
                source_checkout_report = {
                    "kind": "SOURCE_CHECKOUT_ERROR",
                    "actual": str(exc),
                    "reconciliationRequired": True,
                }
                finding("SOURCE_CHECKOUT_ERROR", "ui.source-checkout", "readable git checkout",
                        str(exc))
        source_state_report = _classify_ui_source_state(manifest.get("ui", {}), ui_cfg)
        if source_state_report["kind"] != "CURRENT":
            finding(source_state_report["kind"], "ui.source-state",
                    source_state_report["expected"], source_state_report["actual"])
        payload = {k: v for k, v in manifest.items() if k != "profileCombinationHash"}
        calculated = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode("utf-8")).hexdigest()
        if calculated != manifest.get("profileCombinationHash"):
            finding("CONFIG_DRIFT", "profileCombinationHash", calculated,
                    manifest.get("profileCombinationHash", "missing"))
    return {"status": "PASS" if not findings else "DRIFT", "findings": findings,
            "warnings": warnings, "manifest": manifest,
            "sourceState": source_state_report,
            "sourceCheckout": source_checkout_report}


def apply(home: Path, contract: dict[str, Any], *, check_lock: bool = True) -> dict[str, Any]:
    cfg = _config(contract)
    errors = validate_contract(contract, check_lock=check_lock)
    if errors:
        raise DshCompositionError("invalid DSH composition contract:\n" + "\n".join(errors))
    home.mkdir(parents=True, exist_ok=True)
    current = inspect(home, contract)
    if current["status"] == "PASS" and current["manifest"].get("profileCombinationHash"):
        return {"status": "NO_DRIFT", "transactionId": None,
                "profileCombinationHash": current["manifest"]["profileCombinationHash"],
                "warnings": current["warnings"]}

    stage_root = Path(tempfile.mkdtemp(prefix="dsh-apply-", dir=str(home)))
    cleanup_ui = lambda: None
    try:
        stage_profile = stage_root / "profile"
        stage_profile.mkdir(parents=True, exist_ok=True)
        node_root, node_version, node_hash = _node_runtime(stage_root, home, cfg)
        base_root = _install_base(stage_profile, node_root, cfg, home=home)

        ui_cfg = cfg["ui"]
        live_profile = home / cfg["profile"]["relative_to_dsh_home"]
        live_manifest_path = live_profile / cfg["profile"]["manifest_file"]
        reused_ui = False
        client_dest = None
        web_dest = None
        web_hash = None
        source_state = None

        if live_manifest_path.is_file():
            try:
                live_manifest = json.loads(live_manifest_path.read_text(encoding="utf-8-sig"))
                live_client = _dsh_resolved_dependency_root(base_root, "@deepseek-ai/dsh-client-ui-conversation") / "lib" / "client.js"
                live_dist = _dsh_resolved_dependency_root(base_root, "@deepseek-ai/dsh-web-frontend") / "dist"
                if live_client.is_file() and live_dist.is_dir():
                    client_hash = sha256_file(live_client)
                    dist_hash = sha256_tree(live_dist)
                    if client_hash == live_manifest.get("ui", {}).get("clientBundleSha256") and dist_hash == live_manifest.get("ui", {}).get("webDistSha256"):
                        client_dest = live_client
                        web_dest = live_dist
                        web_hash = dist_hash
                        source_state = live_manifest.get("ui", {}).get("sourceState", str(ui_cfg["baseline_commit"]))
                        reused_ui = True
            except Exception:
                pass

        if not reused_ui:
            source_root, source_state, cleanup_ui = _resolve_harness_root(home, cfg["ui"])
            client, web_dist, web_hash = _build_ui(source_root, node_root, cfg)
            client_dest, web_dest, deployed_web_hash = _copy_ui(base_root, client, web_dist)
            if web_hash != deployed_web_hash:
                raise DshCompositionError("Web static asset tree hash changed during deployment staging")
        overlays = _copy_overlays(stage_profile, cfg)
        patch_text, managed_hash = render_patch(
            home / cfg["profile"]["relative_to_dsh_home"] / cfg["profile"]["patch_file"], cfg)
        patch_stage = stage_profile / cfg["profile"]["patch_file"]
        patch_stage.parent.mkdir(parents=True, exist_ok=True)
        patch_stage.write_text(patch_text, encoding="utf-8")
        launcher_text = _powershell_launcher(cfg)
        launcher_stage = stage_profile / cfg["profile"]["launcher_file"]
        launcher_stage.write_text(launcher_text, encoding="utf-8")
        launcher_hash = sha256_file(launcher_stage)
        archive = cfg["archive_anchor"]
        profile_rel = Path(cfg["profile"]["relative_to_dsh_home"])
        payload: dict[str, Any] = {
            "schemaVersion": 1,
            "compositionId": cfg["id"],
            "node": {"version": node_version, "relativePath": cfg["node"]["relative_to_dsh_home"],
                     "sha256": node_hash},
            "base": {"package": cfg["base"]["package"], "version": cfg["base"]["version"],
                     "entryRelative": str((Path(cfg["profile"]["relative_to_dsh_home"]) /
                                             base_root.name / cfg["base"]["entry_relative_to_distribution"])).replace("\\", "/"),
                     "entrySha256": sha256_file(base_root / cfg["base"]["entry_relative_to_distribution"])},
            "ui": {"repository": cfg["ui"]["repository"], "baselineCommit": cfg["ui"]["baseline_commit"],
                   "sourceState": source_state, "fixCommit": cfg["ui"]["fix_commit"],
                   "patchFile": cfg["ui"]["patch_file"], "patchSha256": cfg["ui"]["patch_sha256"],
                   "clientBundleRelative": str((profile_rel / client_dest.relative_to(stage_profile))).replace("\\", "/"),
                   "clientBundleSha256": sha256_file(client_dest),
                   "webDistRelative": str((profile_rel / web_dest.relative_to(stage_profile))).replace("\\", "/"),
                   "webDistSha256": web_hash},
            "overlays": overlays,
            "cordisPatch": {"relativePath": str((Path(cfg["profile"]["relative_to_dsh_home"]) /
                                                  cfg["profile"]["patch_file"])).replace("\\", "/"),
                            "managedBlockSha256": managed_hash},
            "launcher": {"relativePath": str((Path(cfg["profile"]["relative_to_dsh_home"]) /
                                               cfg["profile"]["launcher_file"])).replace("\\", "/"),
                         "sha256": launcher_hash},
            "archiveAnchor": {"sessionId": archive["session_id"], "status": archive["status"],
                              "operationalLabel": archive["operational_label"],
                              "artifactSha256": archive["artifact_sha256"]},
        }
        manifest, composition_hash = _stable_manifest(payload)
        manifest_stage = stage_profile / cfg["profile"]["manifest_file"]
        manifest_stage.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        base_manifest = {
            "package": cfg["base"]["package"], "version": cfg["base"]["version"],
            "nodeVersion": node_version, "nodeRelativePath": cfg["node"]["relative_to_dsh_home"],
            "entryRelativeToProfile": str((Path(cfg["profile"]["relative_to_dsh_home"]) /
                                             base_root.name / cfg["base"]["entry_relative_to_distribution"])).replace("\\", "/"),
            "installPolicy": cfg["base"]["install_mode"],
            "baseEntrySha256": payload["base"]["entrySha256"],
            "uiBundleSha256": payload["ui"]["clientBundleSha256"],
            "webDistSha256": payload["ui"]["webDistSha256"],
            "compositionHash": composition_hash,
            "forbiddenLaunchers": ["npx --yes @deepseek-ai/dsh", "npx @deepseek-ai/dsh"],
        }
        base_manifest_stage = stage_profile / cfg["profile"]["base_distribution_file"]
        base_manifest_stage.write_text(json.dumps(base_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        entries: list[tuple[str, Path]] = [
            (cfg["node"]["relative_to_dsh_home"], node_root),
            (str(profile_rel / base_root.name), base_root),
        ]
        for plugin in cfg["managed_rows"]["plugins"]:
            entries.append((str(profile_rel / "plugins" / plugin["plugin_directory"]),
                            stage_profile / "plugins" / plugin["plugin_directory"]))
        entries.extend([
            (str(profile_rel / cfg["profile"]["patch_file"]), patch_stage),
            (str(profile_rel / cfg["profile"]["launcher_file"]), launcher_stage),
            (str(profile_rel / cfg["profile"]["manifest_file"]), manifest_stage),
            (str(profile_rel / cfg["profile"]["base_distribution_file"]), base_manifest_stage),
        ])
        txid = _publish(home, entries)
        return {"status": "APPLIED", "transactionId": txid,
                "profileCombinationHash": composition_hash,
                "warnings": inspect(home, contract)["warnings"]}
    except Exception as exc:
        if isinstance(exc, DshCompositionError):
            raise
        raise DshCompositionError(str(exc)) from exc
    finally:
        try:
            cleanup_ui()
        except Exception:
            # Worktree cleanup is best-effort. In particular, Windows can
            # reject deleting long generated dependency paths after a build;
            # never let that mask a successful publish or the original
            # composition failure.
            pass
        finally:
            _remove(stage_root)


def rollback(home: Path, transaction_id: str) -> dict[str, Any]:
    backup = home / ".aic-dsh-backups" / transaction_id
    tx_file = backup / "transaction.json"
    if not tx_file.is_file():
        raise DshCompositionError(f"unknown DSH transaction: {transaction_id}")
    tx = json.loads(tx_file.read_text(encoding="utf-8-sig"))
    if tx.get("status") != "COMMITTED":
        raise DshCompositionError(f"transaction is not committed: {transaction_id}")
    current_backup = home / ".aic-dsh-backups" / f"rollback-{uuid.uuid4().hex}"
    current_backup.mkdir(parents=True, exist_ok=True)
    restored = 0
    for record in reversed(tx.get("entries", [])):
        live = home / record["live"]
        if _lexists(live):
            saved = current_backup / record["live"]
            saved.parent.mkdir(parents=True, exist_ok=True)
            os.replace(live, saved)
        old = home / record["backup"]
        if record.get("hadOriginal") and _lexists(old):
            live.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old, live)
        restored += 1
    tx["status"] = "ROLLED_BACK"
    tx["rollbackCurrent"] = str(current_backup.relative_to(home)).replace("\\", "/")
    tx_file.write_text(json.dumps(tx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "ROLLED_BACK", "transactionId": transaction_id,
            "savedCurrent": tx["rollbackCurrent"], "components": restored}
