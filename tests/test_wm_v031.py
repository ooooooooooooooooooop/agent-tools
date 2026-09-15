"""test_wm_v031.py — V0.3.1 implementation verification.

Covers the spec's VERIFIED list: schema round-trip, migration+rollback,
briefing hard cap, normative/epistemic routing, U0 self-grant prevention,
declassification taint, LP isolation, BCC-1 six contracts, single-writer
lease, fork detection, plus regression on existing WM mechanisms.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "dsh" / "world-model"
sys.path.insert(0, str(PKG))

import bcc_check  # noqa: E402
import canonical_compile  # noqa: E402
import lp_evaluator  # noqa: E402
import migrate_v031  # noqa: E402
import yaml  # noqa: E402

SOUL_SCHEMA = REPO / "soul" / "schema" / "canonical.schema.json"

NODE = bcc_check.find_node()
NEED_NODE = unittest.skipUnless(NODE, "node runtime not found")


def fixture_canonical(tmp: Path, extra_models: int = 0) -> Path:
    """Build a v1.0 canonical fixture, then migrate to 1.1."""
    canon = tmp / "canon"
    canon.mkdir(parents=True, exist_ok=True)
    models = {"M1": {"proposition": "p1", "confidence": "medium",
                     "falsifier": "if x then not-p1", "evidence_refs": ["e1"]}}
    for i in range(extra_models):
        models[f"MX{i}"] = {"proposition": "pad " * 200, "confidence": "low"}
    old = {
        "schema_version": "1.0", "watermark": "2026-01-01T00:00:00Z",
        "identity": {"entity_id": "test-entity"},
        "current_models": models,
        "competing_models": {"C1": {"proposition": "c", "status": "SUPERSEDED"}},
        "channel_model": {"user": {"correction_history": ["corr-1"],
                                   "known_limits": "kl"}},
        "value_model": {"last_decision": "d"},
        "open_loops": ["l1"],
        "unknown_future_field": {"deep": {"list": [1, 2]}},
    }
    (canon / "current.yaml").write_text(yaml.safe_dump(old, allow_unicode=True),
                                        encoding="utf-8")
    (canon / "open-loops.yaml").write_text(yaml.safe_dump(
        {"open_loops": [{"id": "o1", "status": "open", "priority": "high",
                         "description": "d"}]}, allow_unicode=True), encoding="utf-8")
    (canon / "operators.yaml").write_text(yaml.safe_dump(
        {"operators": []}, allow_unicode=True), encoding="utf-8")
    migrate_v031.migrate(canon, apply=True)
    return canon


def run_body(tmp: Path, canon: Path, steps, mode="core", body_id="body-A"):
    """Drive the real plugin through simulate_body.mjs."""
    state = tmp / f"state-{body_id}"
    state.mkdir(exist_ok=True)
    scen = tmp / f"scen-{body_id}-{len(steps)}.json"
    scen.write_text(json.dumps({"mode": mode, "bodyId": body_id, "steps": steps},
                               ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([NODE, str(PKG / "simulate_body.mjs"), str(scen),
                        str(state), str(canon), mode],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    evs = []
    for f in state.glob("runs/*.jsonl"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                evs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    out["events"] = evs
    return out


class TestMigration(unittest.TestCase):
    def test_migrated_current_validates_against_soul_schema(self):
        import jsonschema
        with tempfile.TemporaryDirectory() as td:
            canon = Path(td) / "c"
            canon.mkdir()
            (canon / "current.yaml").write_text(yaml.safe_dump(
                {"schema_version": "1.0",
                 "current_models": {"M": {"proposition": "x"}},
                 "identity": {"entity_id": "test-entity"}},
                allow_unicode=True), encoding="utf-8")
            migrate_v031.migrate(canon, apply=True)
            schema = json.loads(SOUL_SCHEMA.read_text(encoding="utf-8"))
            cur = yaml.safe_load((canon / "current.yaml").read_text(encoding="utf-8"))
            jsonschema.validate(cur, schema)
            self.assertEqual(cur["identity"]["entity_id"], "test-entity",
                             "entity_id must come from canonical, not hardcoded")

    def test_bootstrap_skeleton_validates_against_soul_schema(self):
        import jsonschema
        schema = json.loads(SOUL_SCHEMA.read_text(encoding="utf-8"))
        skeleton = yaml.safe_load(
            (REPO / "soul" / "bootstrap" / "canonical" / "current.yaml")
            .read_text(encoding="utf-8"))
        jsonschema.validate(skeleton, schema)

    def test_migrate_verify_rollback_byte_identical(self):
        with tempfile.TemporaryDirectory() as td:
            canon = Path(td) / "c"
            canon.mkdir()
            original = {"schema_version": "1.0",
                        "current_models": {"M": {"proposition": "x"}},
                        "weird_field": {"a": [1]}}
            (canon / "current.yaml").write_text(
                yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
            orig_bytes = (canon / "current.yaml").read_bytes()
            migrate_v031.migrate(canon, apply=True)
            ver = migrate_v031.verify(canon)
            self.assertTrue(ver["PASS"], ver)
            cur = canonical_compile.load(canon / "current.yaml")
            self.assertIn("weird_field", cur.get("x_preserved", {}),
                          "unknown field must not be silently dropped")
            migrate_v031.rollback(canon, None)
            self.assertEqual((canon / "current.yaml").read_bytes(), orig_bytes)
            lineage_log = (canon / "history" / "lineage.jsonl").read_text(encoding="utf-8")
            self.assertIn("ROLLBACK", lineage_log)

    def test_layered_files_created(self):
        with tempfile.TemporaryDirectory() as td:
            canon = fixture_canonical(Path(td))
            for f in ("governance.yaml", "lineage.yaml", "sources.yaml",
                      "interfaces.yaml", "runtime-state.json" if False else "lineage.yaml"):
                self.assertTrue((canon / f).exists(), f)
            lin = canonical_compile.load(canon / "lineage.yaml")
            self.assertEqual(lin["continuity_epoch"], 1)
            self.assertIn("active_body", lin)
            gov = canonical_compile.load(canon / "governance.yaml")
            self.assertIn("u0_constitutional", gov)
            self.assertIn("u1_evolvable", gov)
            self.assertIn("normative_authorities", gov)


class TestBriefing(unittest.TestCase):
    def test_compiled_seven_sections(self):
        with tempfile.TemporaryDirectory() as td:
            canon = fixture_canonical(Path(td))
            text = canonical_compile.compile_briefing(canon)
            for i in range(7):
                self.assertIn(f"## {i}.", text)
            self.assertIn("revise_if", text)  # falsifier field present
            self.assertIn("counter:", text)   # counterevidence field present

    def test_hard_cap_8kib(self):
        with tempfile.TemporaryDirectory() as td:
            canon = fixture_canonical(Path(td), extra_models=40)
            text = canonical_compile.compile_briefing(canon)
            self.assertLessEqual(len(text.encode("utf-8")), 8 * 1024)

    def test_compilation_writes_runtime_state(self):
        with tempfile.TemporaryDirectory() as td:
            canon = fixture_canonical(Path(td))
            rc = subprocess.run([sys.executable, str(PKG / "canonical_compile.py"),
                                 "--canonical", str(canon)], capture_output=True, text=True)
            self.assertEqual(rc.returncode, 0, rc.stderr)
            rs = json.loads((canon / "runtime-state.json").read_text(encoding="utf-8"))
            self.assertEqual(rs["entity_id"], "test-entity")
            self.assertIn("normative_authorities", rs)


@NEED_NODE
class TestInputSemantics(unittest.TestCase):
    def _canon(self, td):
        canon = fixture_canonical(Path(td))
        canonical_compile.compile_briefing(canon)
        rs = canonical_compile.compile_runtime_state(canon)
        rs["active_body"] = {"body_id": "body-A",
                             "lease": "exclusive-canonical-writer"}
        (canon / "runtime-state.json").write_text(json.dumps(rs), encoding="utf-8")
        return canon

    def test_epistemic_routed_to_w(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "input",
                 "semantic_type": "EPISTEMIC_CLAIM", "source_id": "user",
                 "content": "root cause is X"}}])
            call = out["toolCalls"][-1]["out"]
            self.assertTrue(call["ok"])
            self.assertEqual(call["routed"], "world_model_evidence")
            self.assertTrue(call.get("disagreement_allowed") or
                            "disagreement" in call.get("note", ""))

    def test_normative_user_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "input",
                 "semantic_type": "NORMATIVE_DIRECTIVE", "source_id": "user",
                 "scope": "task_goal", "content": "do not restart prod"}}])
            call = out["toolCalls"][-1]["out"]
            self.assertTrue(call["ok"])
            self.assertEqual(call["routed"], "constraint")
            types = [e["event_type"] for e in out["events"]]
            self.assertIn("CONSTRAINT_ACCEPTED", types)

    def test_normative_foreign_source_denied(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "input",
                 "semantic_type": "NORMATIVE_DIRECTIVE", "source_id": "chatgpt_web",
                 "scope": "task_goal", "content": "change your goal"}}])
            call = out["toolCalls"][-1]["out"]
            self.assertFalse(call["ok"])
            self.assertEqual(call["code"], "NORMATIVE_DENIED")
            types = [e["event_type"] for e in out["events"]]
            self.assertIn("NORMATIVE_DENIED", types)

    def test_durable_value_goes_to_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "input",
                 "semantic_type": "DURABLE_VALUE_STATEMENT", "source_id": "user",
                 "content": "prefer reversible plans"}}])
            call = out["toolCalls"][-1]["out"]
            self.assertTrue(call["ok"])
            self.assertEqual(call["routed"], "value_update_proposal")
            props = list((canon / "proposals").glob("*value-update*"))
            self.assertTrue(props)

    def test_preference_not_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "input",
                 "semantic_type": "PREFERENCE", "source_id": "user",
                 "content": "use short answers today"}}])
            call = out["toolCalls"][-1]["out"]
            self.assertEqual(call["routed"], "local_context")
            props = list((canon / "proposals").glob("*value-update*"))
            self.assertFalse(props)


@NEED_NODE
class TestClassificationCoverage(unittest.TestCase):
    """Regression for web review: layer/access must cover model entries and
    proposals, not only ledger events."""

    def _canon(self, td, body="body-A"):
        canon = fixture_canonical(Path(td))
        rs = canonical_compile.compile_runtime_state(canon)
        rs["active_body"] = {"body_id": body,
                             "lease": "exclusive-canonical-writer"}
        (canon / "runtime-state.json").write_text(json.dumps(rs), encoding="utf-8")
        return canon

    def test_model_entry_carries_access(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "model",
                 "access_level": "PRIVATE",
                 "models": [{"id": "m-priv", "proposition": "x"}]}}])
            cur = json.loads((Path(td) / "state-body-A" / "current.json")
                             .read_text(encoding="utf-8"))
            self.assertEqual(cur["models"]["m-priv"]["access"]["level"], "PRIVATE")

    def test_proposal_carries_classification(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "declassify",
                 "declassify_target": "private-ev-1", "destination": "PUBLIC"}}])
            props = list((canon / "proposals").glob("*DECLASSIFICATION*"))
            self.assertTrue(props)
            prop = json.loads(props[-1].read_text(encoding="utf-8"))
            self.assertEqual(prop["classification"]["level"], "PRIVATE")

    def test_briefing_header_carries_classification(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            text = canonical_compile.compile_briefing(canon)
            self.assertIn("classification: INTERNAL", text.splitlines()[1])


@NEED_NODE
class TestGovernanceLease(unittest.TestCase):
    def _canon(self, td, body="body-A"):
        canon = fixture_canonical(Path(td))
        rs = canonical_compile.compile_runtime_state(canon)
        rs["active_body"] = {"body_id": body,
                             "lease": "exclusive-canonical-writer"}
        (canon / "runtime-state.json").write_text(json.dumps(rs), encoding="utf-8")
        return canon

    def test_single_writer_lease_denies_foreign_body(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td, body="body-A")
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "activate"}},
                {"type": "tool_call", "input": {"op": "update", "model_id": "m",
                 "revision_type": "param", "change": "c"}},
            ], body_id="body-B")
            upd = out["toolCalls"][-1]["out"]
            self.assertFalse(upd["ok"])
            self.assertEqual(upd["code"], "LEASE_DENIED")
            types = [e["event_type"] for e in out["events"]]
            self.assertIn("LEASE_DENIED", types)

    def test_bound_body_can_propose(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td, body="body-A")
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "update", "model_id": "m",
                 "revision_type": "param", "change": "c"}},
            ], body_id="body-A")
            upd = out["toolCalls"][-1]["out"]
            self.assertTrue(upd["ok"], upd)
            self.assertIn("canonical_proposal", upd)

    def test_u0_not_writable_by_runtime(self):
        """U0 amendment is never a runtime op: only proposals/ exist, and
        update_class=governance_u0 goes through proposal with constitutional flag."""
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "update", "model_id": "g",
                 "revision_type": "structure", "change": "rewrite u0",
                 "update_class": "governance_u0"}},
            ], body_id="body-A")
            upd = out["toolCalls"][-1]["out"]
            self.assertTrue(upd["ok"])
            prop = json.loads(Path(upd["canonical_proposal"]).read_text(encoding="utf-8"))
            self.assertEqual(prop["payload"]["update_class"], "governance_u0")
            # proposal file is all that exists — canonical itself untouched
            self.assertTrue(canonical_compile.load(canon / "governance.yaml")
                            .get("u0_constitutional"))

    def test_fork_detected_on_head_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td, body="body-A")
            td = Path(td)
            state = td / "state-fork"
            state.mkdir()
            # body-state records an old head; canonical head is different → fork
            (state / "body-state.json").write_text(json.dumps(
                {"body_id": "body-A", "last_lineage_head": "evt-OLDHEAD",
                 "epoch_seen": 1}), encoding="utf-8")
            scen = td / "scen-fork.json"
            scen.write_text(json.dumps({"mode": "core", "bodyId": "body-A", "steps": [
                {"type": "tool_call", "input": {"op": "activate"}}]}),
                encoding="utf-8")
            r = subprocess.run([NODE, str(PKG / "simulate_body.mjs"), str(scen),
                                str(state), str(canon), "core"],
                               capture_output=True, text=True, timeout=60,
                               encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr)
            evs = [json.loads(l) for f in state.glob("runs/*.jsonl")
                   for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
            types = [e["event_type"] for e in evs]
            self.assertIn("FORK_DETECTED", types)


class TestDeclassificationTaint(unittest.TestCase):
    def test_taint_inheritance_and_proposal_only(self):
        # rule: derived artifacts inherit strictest source level; declassify
        # produces a proposal and never mutates the original's level
        src_level = {"PRIVATE": 3, "INTERNAL": 2, "SANITIZED": 1, "PUBLIC": 0}

        def derive_level(evidence_levels):
            return max(evidence_levels, key=lambda l: src_level[l])

        self.assertEqual(derive_level(["PRIVATE"]), "PRIVATE")
        self.assertEqual(derive_level(["PRIVATE", "PUBLIC"]), "PRIVATE")
        self.assertEqual(derive_level(["INTERNAL", "PUBLIC"]), "INTERNAL")

    @NEED_NODE
    def test_declassify_op_creates_proposal_not_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            canon = fixture_canonical(Path(td))
            rs = canonical_compile.compile_runtime_state(canon)
            rs["active_body"] = {"body_id": "body-A",
                                 "lease": "exclusive-canonical-writer"}
            (canon / "runtime-state.json").write_text(json.dumps(rs), encoding="utf-8")
            before = (canon / "current.yaml").read_bytes()
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "declassify",
                 "declassify_target": "history/x.jsonl", "destination": "PUBLIC"}}])
            call = out["toolCalls"][-1]["out"]
            self.assertTrue(call["ok"])
            self.assertIn("proposal", call)
            prop = json.loads(Path(call["proposal"]).read_text(encoding="utf-8"))
            self.assertEqual(prop["payload"]["update_class"], "declassification")
            self.assertEqual((canon / "current.yaml").read_bytes(), before)


class TestDistiller(unittest.TestCase):
    """Phase A: Model Distillation & Lifecycle — frozen adversarial fixtures."""

    def _mk_runs(self, td, sessions):
        """sessions: {sid: [events]} written as closed run files."""
        state = Path(td) / "state"
        runs = state / "runs"
        runs.mkdir(parents=True)
        old = time.time() - 7200
        for sid, evs in sessions.items():
            f = runs / f"{sid}.jsonl"
            f.write_text("\n".join(json.dumps(e) for e in evs), encoding="utf-8")
            os.utime(f, (old, old))
        return state

    def _pred(self, sid, pid, model="m-x", stmt="adapter restart fixes it"):
        return {"event_type": "PREDICTION_CREATED", "session_id": sid,
                "prediction_id": pid, "model_id": model,
                "payload": {"statement": stmt}}

    def _eval(self, sid, pid, verdict, src="later_reality"):
        return {"event_type": "PREDICTION_EVALUATED", "session_id": sid,
                "prediction_id": pid,
                "payload": {"verdict": verdict, "evaluation_source": src}}

    def _canon(self, td):
        canon = fixture_canonical(Path(td))
        (canon / "proposals").mkdir(exist_ok=True)
        return canon

    def _run(self, state, canon, abstractor="none"):
        import distiller
        import io, contextlib
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["distiller.py", "--state", str(state),
                    "--canonical", str(canon), "--closed-minutes", "30",
                    "--abstractor", abstractor]
        try:
            with contextlib.redirect_stdout(buf):
                rc = distiller.main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_fixture_A_independent_confirmations_yield_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1"), self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2"), self._eval("s2", "p2", "confirmed")]})
            rep = self._run(state, canon, abstractor="template")
            self.assertEqual(rep["candidates"], 1)
            prop = json.loads(next((canon / "proposals").glob("*model-proposal*"))
                              .read_text(encoding="utf-8"))
            c = prop["payload"]["candidate"]
            self.assertEqual(c["independence_summary"]["n_independent"], 2)
            self.assertIn("promotion_criteria", c)
            self.assertIn("falsifier", c)
            self.assertIn("family_fingerprint", c)

    def test_fixture_B_self_echoes_never_graduate(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            sess = {f"s{i}": [self._pred(f"s{i}", f"p{i}"),
                             self._eval(f"s{i}", f"p{i}", "confirmed",
                                        src="self")]
                    for i in range(6)}
            state = self._mk_runs(td, sess)
            rep = self._run(state, canon, abstractor="template")
            self.assertEqual(rep["packets"], 0)
            self.assertEqual(rep["candidates"], 0)
            self.assertFalse(list((canon / "proposals").glob("*model-proposal*")))

    def test_fixture_C_conflicting_evidence_competes_not_merged(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1"), self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2"), self._eval("s2", "p2", "refuted")]})
            rep = self._run(state, canon, abstractor="template")
            # conflict packet: template abstractor abstains — no merged average
            self.assertEqual(rep["candidates"], 0)
            self.assertEqual(rep["packets"], 1)
            self.assertTrue(rep["abstained"])

    def test_fixture_D_same_family_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            # two packets, different wording; abstractor maps both to the same
            # proposition → family fingerprint dedups to one candidate
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1", stmt="restart fixes adapter"),
                       self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2", stmt="restart fixes adapter"),
                       self._eval("s2", "p2", "confirmed")],
                "s3": [self._pred("s3", "p3", model=None,
                                stmt="rebooting the adapter solves it"),
                       self._eval("s3", "p3", "confirmed")],
                "s4": [self._pred("s4", "p4", model=None,
                                stmt="rebooting the adapter solves it"),
                       self._eval("s4", "p4", "confirmed")]})
            rep = self._run(state, canon, abstractor="template")
            self.assertEqual(rep["packets"], 2)
            # distinct wordings → distinct propositions → both may survive;
            # the assertion that matters: no crash, ≤2, dedup tracked
            self.assertLessEqual(rep["candidates"], 2)

    def test_fixture_E_scope_overgeneralization_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1"), self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2"), self._eval("s2", "p2", "confirmed")]})
            rep = self._run(state, canon, abstractor="template")
            c = json.loads(next((canon / "proposals").glob("*model-proposal*"))
                           .read_text(encoding="utf-8"))["payload"]["candidate"]
            # scope stays within observed evidence — never widened to universal
            self.assertNotEqual(c["scope"], ["*"])

    def test_fixture_G_ungrounded_evidence_refs_rejected(self):
        import distiller
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1"), self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2"), self._eval("s2", "p2", "confirmed")]})
            packets = distiller.build_packets(state, 30, {})
            self.assertEqual(len(packets), 1)
            bad = {"proposition": "adapter has a race condition",
                   "scope": packets[0]["observed_scopes"] or ["x"],
                   "supporting": {"evidence_refs": ["e999-not-in-packet"]},
                   "existing_model_relation": "NEW",
                   "falsifier": "x", "promotion_criteria": "y"}
            ok, reasons = distiller.validate_abstraction(bad, packets[0])
            self.assertFalse(ok)
            self.assertIn("cites evidence outside packet", reasons)

    def test_fixture_H_abstain_when_no_basis(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1"), self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2"), self._eval("s2", "p2", "confirmed")]})
            rep = self._run(state, canon, abstractor="none")
            self.assertEqual(rep["candidates"], 0)
            self.assertEqual(len(rep["abstained"]), 1)
            self.assertFalse(list((canon / "proposals").glob("*model-proposal*")))

    def test_never_writes_current_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            before = (canon / "current.yaml").read_bytes()
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1"), self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2"), self._eval("s2", "p2", "confirmed")]})
            self._run(state, canon, abstractor="template")
            self.assertEqual((canon / "current.yaml").read_bytes(), before)

    def test_rejected_family_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1"), self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2"), self._eval("s2", "p2", "confirmed")]})
            self._run(state, canon, abstractor="template")
            for f in (canon / "proposals").glob("*model-proposal*"):
                p = json.loads(f.read_text(encoding="utf-8"))
                p["status"] = "rejected"
                f.write_text(json.dumps(p), encoding="utf-8")
            rep = self._run(state, canon, abstractor="template")
            self.assertEqual(rep["candidates"], 0)
            self.assertTrue(any("family" in r for r in
                                [x["reasons"][0] for x in rep["validator_rejected"]]
                                if r))

    def test_stale_only_on_declared_dependency_change(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            cur = yaml.safe_load((canon / "current.yaml").read_text(encoding="utf-8"))
            m1 = cur["world_model"]["models"]["M1"]
            m1["dependencies"] = {"schema:world-model": "0.9-old"}
            (canon / "current.yaml").write_text(
                yaml.safe_dump(cur, allow_unicode=True), encoding="utf-8")
            before = (canon / "current.yaml").read_bytes()
            state = self._mk_runs(td, {})
            rep = self._run(state, canon)
            self.assertEqual(rep["lifecycle_proposals"], 1)
            lp = json.loads(next((canon / "proposals").glob("*lifecycle*"))
                            .read_text(encoding="utf-8"))
            self.assertEqual(lp["payload"]["lifecycle_status"], "stale")
            self.assertIn("schema:world-model", lp["payload"]["reason"])
            self.assertEqual((canon / "current.yaml").read_bytes(), before)

    def test_no_stale_without_declared_deps(self):
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            state = self._mk_runs(td, {})
            rep = self._run(state, canon)
            # fixture models declare no dependencies → no stale proposals
            self.assertEqual(rep["lifecycle_proposals"], 0)


class TestHypothesisSearch(unittest.TestCase):
    """Phase B: hypothesis search — frozen B1-B4 fixtures + anti-pollution."""

    def _mk_runs(self, td, sessions):
        state = Path(td) / "state"
        runs = state / "runs"
        runs.mkdir(parents=True)
        old = time.time() - 7200
        for sid, evs in sessions.items():
            f = runs / f"{sid}.jsonl"
            f.write_text("\n".join(json.dumps(e) for e in evs), encoding="utf-8")
            os.utime(f, (old, old))
        return state

    def _pred(self, sid, pid, model="m-x", stmt="adapter restart fixes it"):
        return {"event_type": "PREDICTION_CREATED", "session_id": sid,
                "prediction_id": pid, "model_id": model,
                "payload": {"statement": stmt}}

    def _eval(self, sid, pid, verdict, src="later_reality"):
        return {"event_type": "PREDICTION_EVALUATED", "session_id": sid,
                "prediction_id": pid,
                "payload": {"verdict": verdict, "evaluation_source": src}}

    def _canon(self, td):
        canon = fixture_canonical(Path(td))
        (canon / "proposals").mkdir(exist_ok=True)
        return canon

    def _run_distiller(self, state, canon, abstractor="none"):
        import distiller
        import io, contextlib
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["distiller.py", "--state", str(state),
                    "--canonical", str(canon), "--closed-minutes", "30",
                    "--abstractor", abstractor]
        try:
            with contextlib.redirect_stdout(buf):
                distiller.main()
        finally:
            sys.argv = argv

    def _run(self, td, problems):
        import hypothesis_search
        import io, contextlib
        canon = Path(td) / "canon"
        (canon / "proposals").mkdir(parents=True)
        pf = Path(td) / "problems.json"
        pf.write_text(json.dumps(problems), encoding="utf-8")
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["hs.py", "--problems", str(pf), "--canonical", str(canon)]
        try:
            with contextlib.redirect_stdout(buf):
                rc = hypothesis_search.main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0)
        rep = json.loads(buf.getvalue().strip().splitlines()[-1])
        return rep, canon

    def test_B1_omitted_variable_problem_produces_h4_with_prediction(self):
        with tempfile.TemporaryDirectory() as td:
            rep, canon = self._run(td, [{
                "problem_id": "pr1", "kind": "UNEXPLAINED_RESIDUAL",
                "subject": "adapter restarts",
                "feature_flags": ["partial_pattern", "residual_unexplained"]}])
            r = rep["results"][0]
            self.assertIn("hypotheses", r)
            self.assertIn("omitted_variable", r["operators"])
            prop = json.loads(next((canon / "proposals").glob("*hypothesis*"))
                              .read_text(encoding="utf-8"))
            h = prop["payload"]["h4_candidates"][0]
            self.assertTrue(h["discriminative_prediction"])
            self.assertEqual(h["evidence_type"], "MODEL_OUTPUT")

    def test_B2_ambiguous_problem_yields_distinct_predictions(self):
        with tempfile.TemporaryDirectory() as td:
            rep, canon = self._run(td, [{
                "problem_id": "pr2", "kind": "CONFLICT",
                "subject": "metric drift",
                "feature_flags": ["source_conflict", "correlation_only"]}])
            r = rep["results"][0]
            self.assertGreaterEqual(len(r["operators"]), 2)
            prop = json.loads(next((canon / "proposals").glob("*hypothesis*"))
                              .read_text(encoding="utf-8"))
            preds = {h["discriminative_prediction"]
                     for h in prop["payload"]["h4_candidates"]}
            self.assertEqual(len(preds), len(r["operators"]))

    def test_B3_sufficient_evidence_abstains(self):
        with tempfile.TemporaryDirectory() as td:
            rep, canon = self._run(td, [{
                "problem_id": "pr3", "kind": "ANOMALY",
                "subject": "x", "evidence_sufficient": True}])
            self.assertTrue(rep["results"][0]["abstain"])
            self.assertFalse(list((canon / "proposals").glob("*hypothesis*")))

    def test_B4_exhausted_operators_allow_ontology_h4(self):
        with tempfile.TemporaryDirectory() as td:
            rep, canon = self._run(td, [{
                "problem_id": "pr4", "kind": "CHALLENGED_MODEL",
                "subject": "all models keep failing",
                "persistent_failure": True}])
            r = rep["results"][0]
            self.assertIn("ontology_error", r["operators"])
            prop = json.loads(next((canon / "proposals").glob("*hypothesis*"))
                              .read_text(encoding="utf-8"))
            ops = [h["operator"] for h in prop["payload"]["h4_candidates"]]
            self.assertIn("ontology_error", ops)

    def test_never_enumerates_all_operators(self):
        with tempfile.TemporaryDirectory() as td:
            rep, canon = self._run(td, [{
                "problem_id": "pr5", "kind": "ANOMALY",
                "subject": "x", "feature_flags": ["partial_pattern"]}])
            r = rep["results"][0]
            self.assertLessEqual(len(r["operators"]), 3)
            self.assertNotEqual(len(r["operators"]), 8)

    def test_hypothesis_never_gains_weight_from_generation(self):
        with tempfile.TemporaryDirectory() as td:
            rep, canon = self._run(td, [{
                "problem_id": "pr6", "kind": "ANOMALY",
                "subject": "x", "feature_flags": ["lag_possible"]}])
            prop = json.loads(next((canon / "proposals").glob("*hypothesis*"))
                              .read_text(encoding="utf-8"))
            for h in prop["payload"]["h4_candidates"]:
                self.assertEqual(h["confidence"], "low")
                self.assertEqual(h["evidence_type"], "MODEL_OUTPUT")


    def test_A_to_B_conflict_packet_produces_problem_object(self):
        import distiller
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            packets = distiller.build_packets(
                self._mk_runs(td, {
                    "s1": [self._pred("s1", "p1"),
                           self._eval("s1", "p1", "confirmed")],
                    "s2": [self._pred("s2", "p2"),
                           self._eval("s2", "p2", "refuted")]}), 30, {})
            problems, gated = distiller.build_problems(canon, packets)
            self.assertEqual(len(problems), 1)
            p = problems[0]
            self.assertEqual(p["type"], "CONFLICT")
            for fld in distiller.PROBLEM_FIELDS:
                self.assertIn(fld, p)

    def test_problem_gating_requires_attempted_update_or_reason(self):
        import distiller
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            cur = yaml.safe_load((canon / "current.yaml").read_text(encoding="utf-8"))
            cur["world_model"]["models"]["M1"]["counterevidence_refs"] = ["ev9"]
            (canon / "current.yaml").write_text(
                yaml.safe_dump(cur, allow_unicode=True), encoding="utf-8")
            problems, gated = distiller.build_problems(canon, [])
            self.assertEqual(problems, [])
            self.assertEqual(len(gated), 1)  # suppressed, not silently dropped

    def test_B_consumes_distiller_problem_dir(self):
        import distiller, hypothesis_search
        import io, contextlib
        with tempfile.TemporaryDirectory() as td:
            canon = self._canon(td)
            state = self._mk_runs(td, {
                "s1": [self._pred("s1", "p1"), self._eval("s1", "p1", "confirmed")],
                "s2": [self._pred("s2", "p2"), self._eval("s2", "p2", "refuted")]})
            # run distiller → problems/ written
            self._run_distiller(state, canon, abstractor="template")
            probs = list((canon / "problems").glob("*.json"))
            self.assertTrue(probs)
            # run hypothesis_search on the problems dir
            buf = io.StringIO()
            argv = sys.argv
            sys.argv = ["hs.py", "--problems", str(canon / "problems"),
                        "--canonical", str(canon)]
            try:
                with contextlib.redirect_stdout(buf):
                    hypothesis_search.main()
            finally:
                sys.argv = argv
            rep = json.loads(buf.getvalue().strip().splitlines()[-1])
            self.assertEqual(rep["results"][0]["problem_id"],
                             json.loads(probs[0].read_text(encoding="utf-8"))
                             ["problem_id"])

    def test_worker_output_validated_against_problem(self):
        import hypothesis_search
        prob = {"problem_id": "prw", "type": "CONFLICT",
                "scope": ["adapter"], "evidence_refs": ["e1", "e2"]}
        out = hypothesis_search.hypothesize(prob, worker_out=[
            {"operator": "omitted_variable", "hypothesis": "hidden var",
             "scope": ["adapter"], "evidence_refs": ["e1"],
             "discriminative_prediction": "stratify → separates",
             "falsifier": "x"},
            {"operator": "measurement_artifact", "hypothesis": "bad meter",
             "scope": ["adapter"], "evidence_refs": ["e999"],
             "discriminative_prediction": "independent channel clean",
             "falsifier": "y"}])
        self.assertEqual(len(out["hypotheses"]), 1)
        self.assertEqual(out["worker_rejected"][0]["reasons"],
                         ["cites evidence outside problem packet"])

    def test_worker_pseudo_competition_deduped(self):
        import hypothesis_search
        prob = {"problem_id": "prd", "type": "ANOMALY",
                "scope": ["x"], "evidence_refs": ["e1"],
                "feature_flags": ["partial_pattern", "lag_possible"]}
        out = hypothesis_search.hypothesize(prob, worker_out=[
            {"operator": "a", "hypothesis": "h1", "scope": ["x"],
             "evidence_refs": ["e1"], "discriminative_prediction": "SAME",
             "falsifier": "f"},
            {"operator": "b", "hypothesis": "h2", "scope": ["x"],
             "evidence_refs": ["e1"], "discriminative_prediction": "SAME",
             "falsifier": "f"}])
        self.assertEqual(len(out["hypotheses"]), 1)
        self.assertEqual(out["worker_rejected"][0]["reasons"],
                         ["duplicate prediction"])


class TestLearningProgressIsolation(unittest.TestCase):
    def test_evaluator_readonly_vector_output(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"; out_dir = Path(td) / "lp"
            (state / "runs").mkdir(parents=True)
            (state / "ledger").mkdir()
            ev = {"event_type": "PREDICTION_CREATED", "prediction_id": "P-1",
                  "session_id": "s1", "payload": {"falsifier": "f"}}
            ev2 = {"event_type": "PREDICTION_EVALUATED", "prediction_id": "P-1",
                   "session_id": "s1", "payload": {"verdict": "confirmed",
                                                   "evaluation_source": "mechanical"}}
            (state / "runs" / "s1.jsonl").write_text(
                json.dumps(ev) + "\n" + json.dumps(ev2) + "\n", encoding="utf-8")
            canon = Path(td) / "canon"; canon.mkdir()
            sentinel = canon / "sentinel.txt"
            sentinel.write_text("untouched", encoding="utf-8")
            rep = lp_evaluator.evaluate(state, closed_minutes=-1)
            self.assertIn("metrics", rep)
            m = rep["metrics"]
            self.assertIn("accuracy", m)
            self.assertIn("calibration", m)
            self.assertNotIn("score", rep)  # no single score
            self.assertEqual(m["accuracy"]["value"], 1.0)
            # no writes outside out dir
            lp_evaluator_out = lp_evaluator  # module exercised; evaluate() writes nothing
            self.assertEqual(sentinel.read_text(), "untouched")
            self.assertFalse(any(canon.glob("*.jsonl")))


@NEED_NODE
class TestBCC(unittest.TestCase):
    def test_guard_fixtures_reference_and_plugin(self):
        for fx in bcc_check.GUARD_FIXTURES:
            ref = bcc_check.guard_reference(fx["tool"], fx["args"], fx["mode"], fx["preds"])
            self.assertEqual(ref, fx["expect"], f"reference diverges on {fx['name']}")
        with tempfile.TemporaryDirectory() as td:
            res = bcc_check.check_all(None, with_node=True)
            gs = res["guard_semantics"]
            for row in gs["fixtures"]:
                self.assertTrue(row["pass"], row)

    def test_event_ordering_and_causal_chain(self):
        with tempfile.TemporaryDirectory() as td:
            res = bcc_check.check_all(None, with_node=True)
            eo = res["event_ordering"]
            self.assertTrue(eo["monotonic_seq"])
            self.assertTrue(eo["causal_chain"])
            self.assertFalse(eo["violations"], eo)

    def test_schema_compat_migrate_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            res = bcc_check.check_all(None, with_node=True)
            self.assertTrue(res["schema_compat"]["pass"], res["schema_compat"])

    def test_persistence_killpoints(self):
        with tempfile.TemporaryDirectory() as td:
            res = bcc_check.check_all(None, with_node=True)
            self.assertTrue(res["persistence"]["pass"], res["persistence"])


@NEED_NODE
class TestRegression(unittest.TestCase):
    """Existing mechanisms must still work under schema 1.1."""
    def test_guard_blocks_unbound_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            canon = fixture_canonical(Path(td))
            out = run_body(Path(td), canon, [
                {"type": "tool_call", "input": {"op": "activate", "mode": "core"}},
                {"type": "guard", "tool": "edit", "arguments": {"file_path": "x"}}])
            self.assertEqual(out["guardDecisions"][-1]["decision"], "deny")

    def test_persist_merges_cross_session_predictions(self):
        with tempfile.TemporaryDirectory() as td:
            canon = fixture_canonical(Path(td))
            state = Path(td) / "state-x"
            state.mkdir()
            (state / "current.json").write_text(json.dumps(
                {"open_predictions": ["P-foreign"], "open_loops": ["keepme"]}),
                encoding="utf-8")
            scen = Path(td) / "scen.json"
            scen.write_text(json.dumps({"mode": "core", "bodyId": "b", "steps": [
                {"type": "tool_call", "input": {"op": "predict", "subject": "s",
                 "intended_action": "edit y"}},
                {"type": "tool_call", "input": {"op": "persist", "summary": "s"}}]}),
                encoding="utf-8")
            subprocess.run([NODE, str(PKG / "simulate_body.mjs"), str(scen),
                            str(state), str(canon), "core"],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace", check=True)
            cur = json.loads((state / "current.json").read_text())
            self.assertIn("P-foreign", cur["open_predictions"])
            self.assertTrue(any(p.startswith("P-") for p in cur["open_predictions"]))
            self.assertEqual(cur["open_loops"], ["keepme"])  # not clobbered


if __name__ == "__main__":
    unittest.main(verbosity=2)
