"""Unit tests for scripts/memory/provider.py + context_builder.py."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "memory"))

from provider import FileMemoryProvider  # noqa: E402
from context_builder import build_context_package  # noqa: E402

PROV = {"source": "manual", "evidence_ref": "test"}


class TestProvider(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.p = FileMemoryProvider(self.dir, device_id="dev-a")

    def test_write_read(self):
        r = self.p.write(scope="personal", type="semantic", content="user prefers terse output",
                         provenance=PROV, confidence="high", retention="keep")
        got = self.p.read(r["id"])
        self.assertEqual(got["content"], "user prefers terse output")
        self.assertEqual(got["record"]["confidence"], "high")
        self.assertEqual(got["state"]["lifecycle"], "active")

    def test_revision_is_separate_immutable_file(self):
        r = self.p.write(scope="personal", type="semantic", content="v1", provenance=PROV)
        self.p.update(r["id"], "v2")
        revs = list((self.dir / "memory" / "records" / r["id"] / "revisions").glob("*.yaml"))
        self.assertEqual(len(revs), 2)  # append-only, no shared array
        self.assertEqual(self.p.read(r["id"])["content"], "v2")
        self.assertEqual(self.p.read(r["id"], as_of="2000-01-01")["content"], None)

    def test_dedupe_updates_instead_of_duplicating(self):
        r1 = self.p.write(scope="personal", type="semantic", content="same", provenance=PROV)
        r2 = self.p.write(scope="personal", type="semantic", content="same", provenance=PROV)
        self.assertTrue(r2["deduped"])
        self.assertEqual(r1["id"], r2["id"])

    def test_supersede_chain(self):
        old = self.p.write(scope="personal", type="semantic", content="old fact", provenance=PROV)
        new = self.p.write(scope="personal", type="semantic", content="new fact",
                           provenance=PROV, supersedes=[old["id"]])
        self.assertEqual(self.p._state(old["id"])["lifecycle"], "superseded")
        self.assertEqual(self.p.read(new["id"])["record"]["supersedes"], [old["id"]])
        hits = self.p.search("fact", scope="personal")
        self.assertEqual([h["id"] for h in hits], [new["id"]])  # superseded excluded

    def test_forget_tombstone_and_hard_guard(self):
        r = self.p.write(scope="personal", type="episodic", content="temp", provenance=PROV)
        with self.assertRaises(ValueError):
            self.p.forget(r["id"], mode="hard")  # not sensitive -> refused
        self.p.forget(r["id"])
        self.assertEqual(self.p._state(r["id"])["lifecycle"], "forgotten")
        self.assertTrue((self.dir / "memory" / "records" / r["id"]).is_dir())  # tombstone keeps files

    def test_scope_isolation(self):
        self.p.write(scope="project:a", type="semantic", content="alpha fact", provenance=PROV)
        self.p.write(scope="project:b", type="semantic", content="beta fact", provenance=PROV)
        hits = self.p.search("fact", scope="project:a")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["scope"], "project:a")

    def test_import_merge_conflict_flag(self):
        bundle = self.p.export()
        other_dir = Path(tempfile.mkdtemp())
        other = FileMemoryProvider(other_dir, device_id="dev-b")
        other.import_bundle(bundle)
        r = self.p.write(scope="personal", type="semantic", content="x", provenance=PROV)
        b2 = self.p.export()
        res = other.import_bundle(b2)
        self.assertEqual(res["added"], 1)

    def test_no_importance_field_persisted(self):
        r = self.p.write(scope="personal", type="semantic", content="y", provenance=PROV)
        meta = (self.dir / "memory" / "records" / r["id"] / "record.yaml").read_text(encoding="utf-8")
        self.assertNotIn("importance", meta)
        self.assertNotIn("derived_score", meta)


class TestContextBuilder(unittest.TestCase):
    def test_package_scoping_and_provenance(self):
        state = Path(tempfile.mkdtemp())
        proj = Path(tempfile.mkdtemp())
        (proj / ".ai" / "state").mkdir(parents=True)
        (proj / ".ai" / "state" / "state.md").write_text(
            "# s\n## goal\nbuild X\n## current_state\nWIP\n## next_actions\nstep 1\n",
            encoding="utf-8")
        p = FileMemoryProvider(state, device_id="t")
        p.write(scope="project:novel-main", type="decision", content="tier0 tag frozen",
                provenance={"source": "repo", "evidence_ref": "README.md"})
        p.write(scope="project:other-proj", type="semantic", content="unrelated thing",
                provenance=PROV)
        pkg = build_context_package(state, "novel-main", "tier0 status",
                                    project_root=proj, budget_chars=4000)
        self.assertTrue(pkg["project_state"]["available"])
        scopes = {m["scope"] for m in pkg["memories"]}
        self.assertIn("project:novel-main", scopes)
        self.assertNotIn("project:other-proj", scopes)
        for m in pkg["memories"]:
            self.assertIn("source", m["provenance"])


if __name__ == "__main__":
    unittest.main()
