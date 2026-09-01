#!/usr/bin/env python3
"""FileMemoryProvider — MemoryProvider V2.1 file implementation.

Frozen physical layout (amendment A):
  memory/records/<memory-id>/record.yaml          immutable metadata
  memory/records/<memory-id>/state.yaml           mutable lifecycle (merged by (at, device))
  memory/records/<memory-id>/revisions/<rev>.yaml immutable revision objects (append-only)

No shared revisions[] array. Retrieval scores are derived at query time and
never persisted. Git is transport/versioning, not the query engine.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

import yaml

def load_policy(policy_path=None):
    """Load retrieval policy config (registry/retrieval-policy.yaml); falls back to built-in defaults."""
    default = {"retention_prior": {"keep": 1.0, "review": 0.6, "disposable": 0.2},
               "weights": {"retention_prior": 0.4, "recency": 0.2, "relevance": 0.4}}
    candidates = [Path(policy_path)] if policy_path else [
        Path(__file__).resolve().parents[2] / "registry" / "retrieval-policy.yaml"]
    for c in candidates:
        if c.is_file():
            data = yaml.safe_load(c.read_text(encoding="utf-8-sig")) or {}
            r = data.get("retrieval", {}).get("derived_score", {})
            if r.get("retention_prior"):
                default["retention_prior"] = r["retention_prior"]
            if r.get("weights"):
                default["weights"] = r["weights"]
            return default
    return default

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
FORGETTABLE_HARD = "sensitive"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _dump(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}


class FileMemoryProvider:
    def __init__(self, root: str | Path, device_id: str = "unknown",
                 policy_path: str | Path | None = None):
        self.root = Path(root)
        self.records = self.root / "memory" / "records"
        self.records.mkdir(parents=True, exist_ok=True)
        self.device_id = device_id
        self._policy = load_policy(policy_path)

    # ------------------------------------------------------------ write path
    def write(self, *, scope: str, type: str, content: str, provenance: dict,
              confidence: str = "medium", retention: str = "review",
              access_policy: dict | None = None, supersedes: list | None = None,
              by_agent: str = "unknown", requested_model: str = "unknown") -> dict:
        fp = hashlib.sha256(content.encode("utf-8")).hexdigest()
        for meta in self._iter_meta():
            if meta.get("scope") == scope and self._fingerprint(meta["id"]) == fp:
                self.update(meta["id"], content, by_agent=by_agent)
                return {"id": meta["id"], "deduped": True}
        mid = uuid.uuid4().hex[:12]
        rdir = self.records / mid
        (rdir / "revisions").mkdir(parents=True)
        record = {
            "id": mid, "scope": scope, "type": type,
            "created": {"at": _now(), "by_agent": by_agent,
                        "requested_model": requested_model,
                        "device_id": self.device_id},
            "provenance": provenance, "confidence": confidence, "retention": retention,
            "access_policy": access_policy or {"inject": "main-agent", "sensitivity": "normal"},
            "supersedes": supersedes or [],
            "content_fingerprint": fp,
        }
        (rdir / "record.yaml").write_text(_dump(record), encoding="utf-8")
        (rdir / "state.yaml").write_text(_dump(
            {"lifecycle": "active", "updated": {"at": record["created"]["at"],
                                                "device_id": self.device_id}}), encoding="utf-8")
        self._add_revision(rdir, content, by_agent)
        for old in (supersedes or []):
            self._set_lifecycle(old, "superseded")
        return {"id": mid, "deduped": False}

    def _add_revision(self, rdir: Path, content: str, by_agent: str) -> str:
        now_ms = int(time.time() * 1000)
        if hasattr(self, "_last_rev_ms") and now_ms <= self._last_rev_ms:
            now_ms = self._last_rev_ms + 1
        self._last_rev_ms = now_ms
        rev = f"r{now_ms}-{uuid.uuid4().hex[:6]}"
        (rdir / "revisions" / f"{rev}.yaml").write_text(_dump(
            {"rev": rev, "at": _now(), "device_id": self.device_id,
             "by_agent": by_agent, "content": content}), encoding="utf-8")
        return rev

    def _fingerprint(self, mid: str) -> str | None:
        meta = self._meta(mid)
        return meta.get("content_fingerprint") if meta else None

    # ------------------------------------------------------------ read path
    def read(self, mid: str, as_of: str | None = None) -> dict | None:
        meta = self._meta(mid)
        if not meta:
            return None
        revs = self._revisions(mid)
        if as_of:
            revs = [r for r in revs if r["at"] <= as_of]
        return {"record": meta, "state": self._state(mid),
                "content": revs[-1]["content"] if revs else None,
                "revision_count": len(revs)}

    def search(self, query: str = "", *, scope: str | None = None,
               type: str | None = None, min_confidence: str | None = None,
               exclude_states: tuple = ("forgotten", "superseded")) -> list[dict]:
        terms = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))
        out = []
        for meta in self._iter_meta():
            state = self._state(meta["id"])
            if state.get("lifecycle") in exclude_states:
                continue
            if scope and meta.get("scope") != scope:
                continue
            if type and meta.get("type") != type:
                continue
            if min_confidence and CONFIDENCE_RANK.get(meta.get("confidence"), 0) < \
                    CONFIDENCE_RANK[min_confidence]:
                continue
            latest = self.read(meta["id"])
            content = (latest or {}).get("content") or ""
            score = self._derived_score(meta, content, terms)
            out.append({"id": meta["id"], "scope": meta["scope"], "type": meta["type"],
                        "confidence": meta["confidence"], "retention": meta["retention"],
                        "provenance": meta["provenance"], "content": content,
                        "derived_score": round(score, 4)})
        out.sort(key=lambda r: r["derived_score"], reverse=True)
        return out

    def _derived_score(self, meta: dict, content: str, terms: set) -> float:
        """Query-time score: retention prior x recency decay x term overlap. Never persisted."""
        prior = self._policy["retention_prior"].get(meta.get("retention"), 0.5)
        try:
            age_days = max((time.time() - time.mktime(time.strptime(
                meta["created"]["at"][:19], "%Y-%m-%dT%H:%M:%S"))) / 86400, 0)
        except (ValueError, KeyError):
            age_days = 30
        recency = 1.0 / (1.0 + age_days / 30.0)
        if terms:
            hay = content.lower()
            hits = sum(1 for t in terms if t in hay)
            relevance = hits / len(terms)
        else:
            relevance = 1.0
        w = self._policy["weights"]
        return prior * w["retention_prior"] + recency * w["recency"] + relevance * w["relevance"]

    # ------------------------------------------------------------ lifecycle
    def update(self, mid: str, content: str, *, by_agent: str = "unknown") -> dict:
        rdir = self.records / mid
        if not rdir.is_dir():
            raise KeyError(mid)
        rev = self._add_revision(rdir, content, by_agent)
        meta = self._meta(mid)
        meta["content_fingerprint"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        (rdir / "record.yaml").write_text(_dump(meta), encoding="utf-8")
        return {"id": mid, "revision": rev}

    def supersede(self, new_ids_base: dict, supersedes: list) -> dict:
        return self.write(supersedes=supersedes, **new_ids_base)

    def forget(self, mid: str, mode: str = "tombstone") -> dict:
        meta = self._meta(mid)
        if not meta:
            raise KeyError(mid)
        if mode == "hard":
            if meta["access_policy"].get("sensitivity") != FORGETTABLE_HARD:
                raise ValueError("hard delete only allowed for sensitivity=sensitive")
            import shutil
            shutil.rmtree(self.records / mid)
            return {"id": mid, "forgotten": "hard"}
        self._set_lifecycle(mid, "forgotten")
        return {"id": mid, "forgotten": "tombstone"}

    def consolidate(self, *, scope: str, episodic_ids: list, summary: str,
                    provenance: dict, by_agent: str = "unknown") -> dict:
        return self.write(scope=scope, type="semantic", content=summary,
                          provenance=provenance, confidence="high", retention="keep",
                          supersedes=episodic_ids, by_agent=by_agent)

    def sweep(self, stale_days: int = 90) -> list[dict]:
        """Report-only lifecycle sweep: retention=review records untouched for stale_days."""
        cutoff = time.time() - stale_days * 86400
        out = []
        for meta in self._iter_meta():
            state = self._state(meta["id"])
            if state.get("lifecycle") != "active" or meta.get("retention") != "review":
                continue
            try:
                ts = time.mktime(time.strptime(state["updated"]["at"][:19], "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, KeyError):
                continue
            if ts < cutoff:
                out.append({"id": meta["id"], "scope": meta["scope"],
                            "stale_since": state["updated"]["at"]})
        return out

    # ------------------------------------------------------------ portability
    def export(self, scope: str | None = None) -> dict:
        bundle = {"format": "memory-bundle/v1", "exported_at": _now(), "records": []}
        for meta in self._iter_meta():
            if scope and meta.get("scope") != scope:
                continue
            bundle["records"].append({
                "record": meta, "state": self._state(meta["id"]),
                "revisions": self._revisions(meta["id"])})
        return bundle

    def import_bundle(self, bundle: dict) -> dict:
        merged = conflicts = added = 0
        for item in bundle.get("records", []):
            mid = item["record"]["id"]
            rdir = self.records / mid
            if not rdir.is_dir():
                rdir.mkdir(parents=True)
                (rdir / "revisions").mkdir(exist_ok=True)
                (rdir / "record.yaml").write_text(_dump(item["record"]), encoding="utf-8")
                (rdir / "state.yaml").write_text(_dump(item["state"]), encoding="utf-8")
                for rev in item["revisions"]:
                    (rdir / "revisions" / f"{rev['rev']}.yaml").write_text(
                        _dump(rev), encoding="utf-8")
                added += 1
                continue
            have = {r["rev"] for r in self._revisions(mid)}
            incoming = {r["rev"] for r in item["revisions"]}
            divergent = bool(have - incoming) and bool(incoming - have)
            for rev in item["revisions"]:
                if rev["rev"] not in have:
                    (rdir / "revisions" / f"{rev['rev']}.yaml").write_text(
                        _dump(rev), encoding="utf-8")
                    merged += 1
            local_state = self._state(mid)
            remote_state = item["state"]
            if local_state.get("lifecycle") != remote_state.get("lifecycle"):
                key = lambda s: (s.get("updated", {}).get("at", ""), s.get("updated", {}).get("device_id", ""))
                winner = max([local_state, remote_state], key=key)
                winner = dict(winner)
                if local_state.get("lifecycle") != remote_state.get("lifecycle"):
                    winner["conflict"] = "lifecycle-divergence"
                    conflicts += 1
                (rdir / "state.yaml").write_text(_dump(winner), encoding="utf-8")
            elif divergent:
                # concurrent immutable revisions from different devices: keep ALL
                # revisions (never silently overwrite); flag conflict; resolution
                # requires an explicit later supersede, not auto text merge.
                st = dict(local_state); st["conflict"] = "concurrent-revisions"
                (rdir / "state.yaml").write_text(_dump(st), encoding="utf-8")
                conflicts += 1
        return {"added": added, "merged_revisions": merged, "conflicts": conflicts}

    # ------------------------------------------------------------ internals
    def _iter_meta(self):
        for rdir in sorted(self.records.iterdir()):
            if rdir.is_dir():
                meta = self._meta(rdir.name)
                if meta:
                    yield meta

    def _meta(self, mid: str) -> dict | None:
        p = self.records / mid / "record.yaml"
        return _load(p) if p.is_file() else None

    def _state(self, mid: str) -> dict:
        p = self.records / mid / "state.yaml"
        return _load(p) if p.is_file() else {"lifecycle": "active"}

    def _set_lifecycle(self, mid: str, lifecycle: str) -> None:
        (self.records / mid / "state.yaml").write_text(_dump(
            {"lifecycle": lifecycle,
             "updated": {"at": _now(), "device_id": self.device_id}}), encoding="utf-8")

    def _revisions(self, mid: str) -> list:
        rdir = self.records / mid / "revisions"
        if not rdir.is_dir():
            return []
        revs = [_load(p) for p in rdir.glob("*.yaml")]
        revs.sort(key=lambda r: (r.get("at", ""), r.get("rev", "")))
        return revs


if __name__ == "__main__":
    import sys

    prov = FileMemoryProvider(sys.argv[1] if len(sys.argv) > 1 else ".", device_id="cli")
    print(json.dumps({"records": [m["id"] for m in prov._iter_meta()]},
                     ensure_ascii=False))
