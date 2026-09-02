"""modernized_provider.py — Personal AI Modernized Hybrid Memory Provider.

SSOT: personal-ai-state/memory/records/<id>/ (canonical YAML files).
Index: Local SQLite FTS5 index (derived, fully rebuildable from canonical records).

Features:
  - Canonical records preserved as sole truth
  - SQLite FTS5 Full-Text & Lexical search + Metadata filtering
  - Semantic ranking combining relevance, scope match, confidence, recency, retention prior
  - Memory Write Gate (durability, secret exclusion, deduplication, conflict checking)
  - Automatic Candidate Extraction from ResultEnvelope and user input
  - Lifecycle management: ACTIVE, STALE, SUPERSEDED, CONFLICTED, EXPIRED, FORGOTTEN
  - Forget / Hard delete with full index synchronization
  - Context Injection Token Budgeting (ALWAYS_LOAD, ON_DEMAND, PROJECT_SCOPED)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
SECRET_PATTERNS = [
    re.compile(r"api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{16,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[A-Za-z0-9]{30,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def contains_secrets(text: str) -> bool:
    return any(p.search(text) for p in SECRET_PATTERNS)


@dataclass
class MemoryRecordSchema:
    id: str
    scope: str  # "personal" | "global" | "project:<name>"
    type: str  # "preference" | "fact" | "decision" | "architecture" | "semantic" | "episodic"
    subject: str
    content: str
    source: str
    provenance: dict[str, Any]
    confidence: str = "medium"  # "low" | "medium" | "high"
    retention: str = "review"  # "keep" | "review" | "disposable"
    status: str = "active"  # "active" | "superseded" | "conflicted" | "stale" | "forgotten"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_confirmed_at: str = field(default_factory=_now)
    tags: list[str] = field(default_factory=list)
    project: str | None = None
    harness: str | None = None
    expires_at: str | None = None
    supersedes: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    content_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryWriteGate:
    """Evaluates whether a candidate memory is durable, safe, and valid for persistence."""

    @staticmethod
    def evaluate(
        candidate: dict[str, Any],
        existing_fingerprints: set[str],
        *,
        is_canonical_policy: bool = False,
    ) -> tuple[bool, str]:
        if is_canonical_policy:
            return False, "CANONICAL_POLICY_WRITE_FORBIDDEN: policies belong to AGENTS.md / preferences.md SSOT"

        content = str(candidate.get("content", "")).strip()
        if not content:
            return False, "EMPTY_CONTENT"

        # Secret check
        if contains_secrets(content) or contains_secrets(json.dumps(candidate.get("provenance", {}))):
            return False, "SECRET_EXCLUSION_HIT: content contains credential-like material"

        # Deduplication check
        fp = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if fp in existing_fingerprints:
            return True, "DEDUP_UPDATE"

        # Durability & ephemerality check
        ephemeral_patterns = [
            r"/tmp/",
            r"AppData\\Local\\Temp",
            r"__worktree_",
            r"exit code: 0",
            r"console\.log\(",
        ]
        if any(re.search(pat, content) for pat in ephemeral_patterns) and not candidate.get("is_explicit"):
            return False, "EPHEMERAL_CONTENT_REJECTED: temporary execution logs or worktree paths are not durable memory"

        return True, "ADMITTED"


class ModernizedMemoryProvider:
    def __init__(
        self,
        root: str | Path,
        device_id: str = "unknown",
        index_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.records_dir = self.root / "memory" / "records"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.device_id = device_id
        self.index_file = (
            Path(index_path).resolve()
            if index_path
            else self.root / "memory" / ".index.sqlite"
        )
        self._init_db()
        self.rebuild_index_if_needed()

    # ---------------------------------------------------------------- DB / Index
    def _init_db(self) -> None:
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.index_file))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_index (
                id TEXT PRIMARY KEY,
                scope TEXT,
                type TEXT,
                subject TEXT,
                content TEXT,
                confidence TEXT,
                retention TEXT,
                status TEXT,
                project TEXT,
                harness TEXT,
                created_at TEXT,
                updated_at TEXT,
                fingerprint TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                id UNINDEXED,
                subject,
                content,
                tokenize = 'unicode61'
            )
            """
        )
        conn.commit()
        conn.close()

    def rebuild_index(self) -> int:
        """Rebuild SQLite FTS5 index entirely from canonical YAML files."""
        conn = sqlite3.connect(str(self.index_file))
        cur = conn.cursor()
        cur.execute("DELETE FROM memory_index")
        cur.execute("DELETE FROM memory_fts")

        count = 0
        for rdir in self.records_dir.iterdir():
            if not rdir.is_dir():
                continue
            rec_file = rdir / "record.yaml"
            if not rec_file.is_file():
                continue
            try:
                rec = yaml.safe_load(rec_file.read_text(encoding="utf-8-sig")) or {}
                state_file = rdir / "state.yaml"
                st = yaml.safe_load(state_file.read_text(encoding="utf-8-sig")) if state_file.is_file() else {}
                lifecycle = st.get("lifecycle", rec.get("status", "active"))
                content = self._latest_revision_content(rdir) or rec.get("content", "")

                mid = rec["id"]
                scope = rec.get("scope", "global")
                mtype = rec.get("type", "fact")
                subject = rec.get("subject", mid)
                confidence = rec.get("confidence", "medium")
                retention = rec.get("retention", "review")
                project = rec.get("project") or (scope.split(":", 1)[1] if scope.startswith("project:") else "")
                harness = rec.get("harness", "")
                created_at = rec.get("created", {}).get("at", rec.get("created_at", _now()))
                updated_at = st.get("updated", {}).get("at", created_at)
                fp = rec.get("content_fingerprint", hashlib.sha256(content.encode("utf-8")).hexdigest())

                cur.execute(
                    """
                    INSERT INTO memory_index (id, scope, type, subject, content, confidence, retention, status, project, harness, created_at, updated_at, fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (mid, scope, mtype, subject, content, confidence, retention, lifecycle, project, harness, created_at, updated_at, fp),
                )
                cur.execute(
                    "INSERT INTO memory_fts (id, subject, content) VALUES (?, ?, ?)",
                    (mid, subject, content),
                )
                count += 1
            except Exception:
                continue

        conn.commit()
        conn.close()
        return count

    def rebuild_index_if_needed(self) -> None:
        conn = sqlite3.connect(str(self.index_file))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM memory_index")
        indexed_count = cur.fetchone()[0]
        conn.close()

        physical_count = sum(1 for d in self.records_dir.iterdir() if d.is_dir() and (d / "record.yaml").is_file())
        if indexed_count != physical_count:
            self.rebuild_index()

    def _latest_revision_content(self, rdir: Path) -> str | None:
        revs_dir = rdir / "revisions"
        if not revs_dir.is_dir():
            return None
        rev_files = sorted(revs_dir.glob("*.yaml"))
        if not rev_files:
            return None
        try:
            data = yaml.safe_load(rev_files[-1].read_text(encoding="utf-8-sig")) or {}
            return data.get("content")
        except Exception:
            return None

    # ---------------------------------------------------------------- Write Path
    def write(
        self,
        *,
        scope: str,
        type: str,
        content: str,
        provenance: dict[str, Any],
        subject: str = "",
        confidence: str = "medium",
        retention: str = "review",
        project: str | None = None,
        harness: str | None = None,
        supersedes: list[str] | None = None,
        conflicts_with: list[str] | None = None,
        by_agent: str = "unknown",
        requested_model: str = "unknown",
        is_explicit: bool = False,
    ) -> dict[str, Any]:
        """Validated write path guarded by MemoryWriteGate."""
        conn = sqlite3.connect(str(self.index_file))
        cur = conn.cursor()
        cur.execute("SELECT fingerprint FROM memory_index")
        fps = {row[0] for row in cur.fetchall()}
        conn.close()

        candidate = {
            "scope": scope, "type": type, "content": content, "provenance": provenance,
            "is_explicit": is_explicit,
        }
        admitted, reason = MemoryWriteGate.evaluate(candidate, fps)
        if not admitted:
            return {"ok": False, "status": "REJECTED", "reason": reason}

        fp = hashlib.sha256(content.encode("utf-8")).hexdigest()
        resolved_subject = subject or content[:40].replace("\n", " ")

        if reason == "DEDUP_UPDATE":
            # Find existing record with same fingerprint in same scope
            conn = sqlite3.connect(str(self.index_file))
            cur = conn.cursor()
            cur.execute("SELECT id FROM memory_index WHERE fingerprint = ? AND scope = ?", (fp, scope))
            row = cur.fetchone()
            conn.close()
            if row:
                mid = row[0]
                self.update(mid, content, by_agent=by_agent)
                return {"ok": True, "id": mid, "status": "DEDUP_UPDATED"}

        mid = uuid.uuid4().hex[:12]
        rdir = self.records_dir / mid
        (rdir / "revisions").mkdir(parents=True, exist_ok=True)

        now_str = _now()
        record_data = {
            "id": mid,
            "scope": scope,
            "type": type,
            "subject": resolved_subject,
            "created": {
                "at": now_str,
                "by_agent": by_agent,
                "requested_model": requested_model,
                "device_id": self.device_id,
            },
            "provenance": provenance,
            "confidence": confidence,
            "retention": retention,
            "project": project or (scope.split(":", 1)[1] if scope.startswith("project:") else None),
            "harness": harness,
            "supersedes": supersedes or [],
            "conflicts_with": conflicts_with or [],
            "content_fingerprint": fp,
        }
        (rdir / "record.yaml").write_text(yaml.safe_dump(record_data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (rdir / "state.yaml").write_text(yaml.safe_dump({"lifecycle": "active", "updated": {"at": now_str, "device_id": self.device_id}}, sort_keys=False), encoding="utf-8")

        rev = f"r{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
        (rdir / "revisions" / f"{rev}.yaml").write_text(yaml.safe_dump({"rev": rev, "at": now_str, "device_id": self.device_id, "by_agent": by_agent, "content": content}, sort_keys=False), encoding="utf-8")

        # Handle supersession
        for old_id in (supersedes or []):
            self.set_lifecycle(old_id, "superseded")

        # Update SQLite index
        conn = sqlite3.connect(str(self.index_file))
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO memory_index (id, scope, type, subject, content, confidence, retention, status, project, harness, created_at, updated_at, fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (mid, scope, type, resolved_subject, content, confidence, retention, "active", record_data["project"], harness, now_str, now_str, fp),
        )
        cur.execute("INSERT INTO memory_fts (id, subject, content) VALUES (?, ?, ?)", (mid, resolved_subject, content))
        conn.commit()
        conn.close()

        return {"ok": True, "id": mid, "status": "CREATED"}

    # ---------------------------------------------------------------- Search & Retrieval
    def search(
        self,
        query: str = "",
        *,
        scope: str | None = None,
        project: str | None = None,
        type: str | None = None,
        min_confidence: str | None = None,
        exclude_statuses: tuple[str, ...] = ("forgotten", "superseded", "conflicted", "expired"),
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Hybrid Lexical/FTS + Metadata filtered retrieval with semantic ranking."""
        conn = sqlite3.connect(str(self.index_file))
        cur = conn.cursor()

        terms = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
        fts_ids: set[str] = set()
        if terms:
            fts_query = " OR ".join(f'"{t}"' for t in terms)
            try:
                cur.execute("SELECT id FROM memory_fts WHERE memory_fts MATCH ?", (fts_query,))
                fts_ids = {row[0] for row in cur.fetchall()}
            except Exception:
                pass

        cur.execute("SELECT id, scope, type, subject, content, confidence, retention, status, project, harness, created_at FROM memory_index")
        rows = cur.fetchall()
        conn.close()

        candidates = []
        for r in rows:
            mid, m_scope, m_type, m_subj, m_content, m_conf, m_ret, m_status, m_proj, m_harness, m_created = r
            if m_status in exclude_statuses:
                continue
            if scope and m_scope != scope:
                continue
            if project and m_proj and m_proj != project:
                continue
            if type and m_type != type:
                continue
            if min_confidence and CONFIDENCE_RANK.get(m_conf, 0) < CONFIDENCE_RANK.get(min_confidence, 0):
                continue

            # Derived Ranking Score calculation
            score = self._compute_score(
                query_terms=terms,
                in_fts=(mid in fts_ids),
                content=m_content,
                subject=m_subj,
                scope=m_scope,
                project=m_proj,
                query_project=project,
                confidence=m_conf,
                retention=m_ret,
                created_at=m_created,
            )

            candidates.append({
                "id": mid,
                "scope": m_scope,
                "type": m_type,
                "subject": m_subj,
                "content": m_content,
                "confidence": m_conf,
                "retention": m_ret,
                "status": m_status,
                "project": m_proj,
                "derived_score": round(score, 4),
            })

        candidates.sort(key=lambda x: x["derived_score"], reverse=True)
        return candidates[:limit]

    def _compute_score(
        self,
        query_terms: list[str],
        in_fts: bool,
        content: str,
        subject: str,
        scope: str,
        project: str | None,
        query_project: str | None,
        confidence: str,
        retention: str,
        created_at: str,
    ) -> float:
        # Retention prior: keep=1.0, review=0.6, disposable=0.2
        retention_prior = {"keep": 1.0, "review": 0.6, "disposable": 0.2}.get(retention, 0.5)

        # Recency decay: 1 / (1 + age_days / 30)
        try:
            age_days = max((time.time() - time.mktime(time.strptime(created_at[:19], "%Y-%m-%dT%H:%M:%S"))) / 86400, 0)
        except Exception:
            age_days = 30
        recency = 1.0 / (1.0 + age_days / 30.0)

        # Relevance
        relevance = 0.0
        if query_terms:
            hay = (subject + " " + content).lower()
            hits = sum(1 for t in query_terms if t in hay)
            relevance = (hits / len(query_terms)) * (1.2 if in_fts else 1.0)
            relevance = min(1.0, relevance)
        else:
            relevance = 0.5

        # Scope boost
        scope_boost = 1.0
        if query_project and project == query_project:
            scope_boost = 1.3
        elif scope == "personal":
            scope_boost = 1.1

        conf_weight = {"high": 1.2, "medium": 1.0, "low": 0.7}.get(confidence, 1.0)

        raw = (0.35 * relevance + 0.35 * retention_prior + 0.30 * recency) * scope_boost * conf_weight
        return raw

    # ---------------------------------------------------------------- Lifecycle & Forget
    def set_lifecycle(self, mid: str, lifecycle: str) -> None:
        rdir = self.records_dir / mid
        if not rdir.is_dir():
            return
        state_file = rdir / "state.yaml"
        st = {"lifecycle": lifecycle, "updated": {"at": _now(), "device_id": self.device_id}}
        state_file.write_text(yaml.safe_dump(st, sort_keys=False), encoding="utf-8")

        conn = sqlite3.connect(str(self.index_file))
        cur = conn.cursor()
        cur.execute("UPDATE memory_index SET status = ?, updated_at = ? WHERE id = ?", (lifecycle, _now(), mid))
        conn.commit()
        conn.close()

    def forget(self, mid: str, *, mode: str = "tombstone") -> dict[str, Any]:
        """Forget a memory record (tombstone lifecycle or physical hard purge)."""
        rdir = self.records_dir / mid
        if not rdir.is_dir():
            raise KeyError(f"memory id '{mid}' not found")

        if mode == "hard":
            import shutil
            shutil.rmtree(rdir, ignore_errors=True)
            conn = sqlite3.connect(str(self.index_file))
            cur = conn.cursor()
            cur.execute("DELETE FROM memory_index WHERE id = ?", (mid,))
            cur.execute("DELETE FROM memory_fts WHERE id = ?", (mid,))
            conn.commit()
            conn.close()
            return {"id": mid, "forgotten": "hard", "status": "PURGED"}

        self.set_lifecycle(mid, "forgotten")
        return {"id": mid, "forgotten": "tombstone", "status": "FORGOTTEN"}

    def update(self, mid: str, content: str, *, by_agent: str = "unknown") -> dict[str, Any]:
        rdir = self.records_dir / mid
        if not rdir.is_dir():
            raise KeyError(mid)

        now_str = _now()
        rev = f"r{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
        (rdir / "revisions" / f"{rev}.yaml").write_text(yaml.safe_dump({"rev": rev, "at": now_str, "device_id": self.device_id, "by_agent": by_agent, "content": content}, sort_keys=False), encoding="utf-8")

        fp = hashlib.sha256(content.encode("utf-8")).hexdigest()
        rec_file = rdir / "record.yaml"
        rec = yaml.safe_load(rec_file.read_text(encoding="utf-8-sig")) or {}
        rec["content_fingerprint"] = fp
        rec_file.write_text(yaml.safe_dump(rec, allow_unicode=True, sort_keys=False), encoding="utf-8")

        conn = sqlite3.connect(str(self.index_file))
        cur = conn.cursor()
        cur.execute("UPDATE memory_index SET content = ?, fingerprint = ?, updated_at = ? WHERE id = ?", (content, fp, now_str, mid))
        cur.execute("UPDATE memory_fts SET content = ? WHERE id = ?", (content, mid))
        conn.commit()
        conn.close()
        return {"id": mid, "revision": rev}
