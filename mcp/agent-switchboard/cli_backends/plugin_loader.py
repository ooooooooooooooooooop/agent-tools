"""plugin_loader.py — directory-scan discovery of user-local CLI backend plugins.

Allows a user to add a new CLI backend by dropping ONE Python file into a plugins
directory (no need to edit the main package or import anything). Aligns with the
classic "scan a directory, register a plugin" pattern (e.g. omnigent's
``~/.omnigent/plugins/``) while staying stdlib-only and dependency-free.

Per-file contract (any of these is accepted):

1. A module-level ``backend`` attribute that is a ``CliBackend`` instance:
       backend = GenericCliBackend("mycli", {...})   # or a custom subclass

2. A module-level ``make_backend()`` callable returning a ``CliBackend``:
       def make_backend(): return MyCliBackend()

3. The module itself is a ``CliBackend`` subclass:
       class MyCliBackend(CliBackend): ...

Optional integrity metadata (aligned with omnigent's plugin checksums):
   CLI_PLUGIN_META = {
       "name": "mycli",         # optional override for the registry name
       "version": "1.0.0",
       "description": "...",
       "sha256": "<hex digest of this file>",   # optional; verified by default
   }

A sibling ``plugin.json`` may also carry ``{ "name": ..., "sha256": ... }``; it takes
precedence over CLI_PLUGIN_META for those keys and can pin a checksum for the ``.py``.

Security: each file is executed with ``importlib`` under the current interpreter (same
trust model as any local config). If ``CLI_PLUGIN_META["sha256"]`` or a ``plugin.json``
checksum is present but does not match the file, the plugin is skipped by default
(``strict=True``) and reported; pass ``strict=False`` to load anyway.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from cli_backend_base import CliBackend, CliRegistry


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_plugin_json(plugin_dir: Path) -> dict[str, Any]:
    """Read optional plugin.json metadata next to a .py plugin (present + valid only)."""
    jfile = plugin_dir / "plugin.json"
    try:
        if jfile.is_file():
            return json.loads(jfile.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _expected_sha(meta: dict[str, Any], plugin_json: dict[str, Any], path: Path) -> str | None:
    """Resolve the expected sha256 for a plugin file, if any was declared."""
    for source in (plugin_json, meta):
        expected = source.get("sha256") or source.get("checksum")
        if isinstance(expected, str) and expected.strip():
            return str(expected).strip()
    return None


def _verify_plugin(
    path: Path,
    meta: dict[str, Any],
    plugin_json: dict[str, Any],
    strict: bool,
) -> tuple[bool, str | None]:
    expected = _expected_sha(meta, plugin_json, path)
    if not expected:
        return True, None
    actual = _file_sha256(path)
    if actual.lower() == expected.lower():
        return True, None
    msg = f"sha256 mismatch for {path.name}: declared {expected}, got {actual}"
    return (not strict, msg)


def _load_plugin_module(path: Path):
    """Import a single .py plugin file as a module (fresh, not cached sys.modules)."""
    mod_name = f"_agent_swb_cli_plugin_{path.stem}"
    # Ensure a stale module from a previous/reloaded run doesn't leak.
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create spec for plugin {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    return module


def _extract_backend(module, path: Path, default_name: str) -> CliBackend | None:
    """Pull a CliBackend from a loaded module per the three accepted contracts."""
    # Contract 1: a module-level `backend` instance.
    attr = getattr(module, "backend", None)
    if isinstance(attr, CliBackend):
        return attr
    # Contract 2: a `make_backend()` factory.
    factory = getattr(module, "make_backend", None)
    if callable(factory):
        try:
            made = factory()
        except Exception:  # noqa: BLE001
            made = None
        if isinstance(made, CliBackend):
            return made
    # Contract 3: the module itself defines a CliBackend subclass; instantiate the
    # last/most obvious one (prefer one whose name matches the file).
    candidates = [
        obj
        for name, obj in vars(module).items()
        if isinstance(obj, type) and issubclass(obj, CliBackend) and obj is not CliBackend
        and _has_overrides(obj)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda cls: (0 if cls.__name__.lower().replace("_", "") == default_name.lower().replace("_", "") else 1)
    )
    try:
        return candidates[0]()
    except Exception:  # noqa: BLE001
        return None


def _has_overrides(cls: type) -> bool:
    """Check that the CliBackend subclass overrides at least one of the two core methods
    (discover or execute), so a bare pass-through subclass isn't silently accepted."""
    base = CliBackend
    for method in ("discover", "execute", "parse_output", "build_command"):
        cls_method = cls.__dict__.get(method)
        base_method = base.__dict__.get(method)
        if cls_method is not None and cls_method is not base_method:
            return True
    return False


def backends_from_directory(
    directory: str | Path | None,
    strict: bool = True,
) -> tuple[list[CliBackend], list[str]]:
    """Scan ``directory`` for CLI backend plugin files and return (backends, errors).

    ``directory`` may be None (no-op). Files named ``__init__.py`` are skipped, as are
    non-``.py`` files. ``strict`` controls whether a sha256 mismatch blocks loading.
    """
    if not directory:
        return [], []
    plugin_dir = Path(directory)
    if not plugin_dir.is_dir():
        return [], []
    backends: list[CliBackend] = []
    errors: list[str] = []
    for path in sorted(plugin_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        default_name = path.stem
        plugin_json = _load_plugin_json(path.parent)
        try:
            module = _load_plugin_module(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name}: import failed: {type(exc).__name__}: {exc}")
            continue
        meta = getattr(module, "CLI_PLUGIN_META", {}) or {}
        ok, msg = _verify_plugin(path, meta, plugin_json, strict)
        if not ok:
            errors.append(f"{path.name}: {msg}")
            if strict:
                continue
        try:
            backend = _extract_backend(module, path, default_name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name}: backend extraction failed: {type(exc).__name__}: {exc}")
            continue
        if backend is None:
            errors.append(
                f"{path.name}: no `backend`/`make_backend`/CliBackend subclass found "
                "(see cli_backends/plugin_loader.py for the contract)"
            )
            continue
        # Honor optional name/description overrides from CLI_PLUGIN_META.
        declared_name = (meta.get("name") if isinstance(meta, dict) else None) or None
        declared_desc = (meta.get("description") if isinstance(meta, dict) else None) or None
        if isinstance(declared_name, str) and declared_name.strip():
            backend = _NameOverrideBackend(backend, declared_name.strip(), declared_desc or None)
        elif isinstance(declared_desc, str) and declared_desc.strip():
            backend = _NameOverrideBackend(backend, backend.name, declared_desc.strip())
        backends.append(backend)
    return backends, errors


def register_backends_from_directory(
    registry: CliRegistry,
    directory: str | Path | None,
    strict: bool = True,
) -> list[str]:
    """Scan ``directory`` and register each discovered backend onto ``registry``.

    Returns a list of human-readable log/errors: one per successfully registered plugin
    and one per failure. Backends whose ``name`` already exists are skipped (reported).
    """
    backends, errors = backends_from_directory(directory, strict=strict)
    notes: list[str] = []
    for b in backends:
        if registry.get(b.name) is not None:
            notes.append(f"{directory}: backend '{b.name}' already registered; skipped plugin file")
            continue
        registry.register(b)
        notes.append(f"plugin_backend: registered '{b.name}' from {directory}")
    notes.extend(errors)
    return notes


class _NameOverrideBackend(CliBackend):
    """Adapter that overlays a new registry name/description onto an inner backend."""

    def __init__(self, inner: CliBackend, name: str, description: str | None = None) -> None:
        self._inner = inner
        self._name = name
        self._description = description or getattr(inner, "description", inner.name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def aliases(self) -> list[str]:
        return list(getattr(self._inner, "aliases", []))

    @property
    def family(self) -> str:
        return getattr(self._inner, "family", "custom")

    @property
    def description(self) -> str:
        return self._description

    def discover(self) -> str | None:
        return self._inner.discover()

    def metadata(self) -> dict[str, Any]:
        md = dict(self._inner.metadata())
        md["name"] = self._name
        if self._description != getattr(self._inner, "description", None):
            md["description"] = self._description
        md["plugin_wrapper"] = True
        return md

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.execute(*args, **kwargs)
