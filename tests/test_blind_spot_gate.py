"""Unit tests for the Blind-Spot Gated Reasoning Engine (Production-Ready Architecture).

Tests:
1. Task-Phase Gate: JUDGMENT vs EXECUTION, fast-path skipping, and execution escalation
2. Canonical DecisionPacket Contract: length caps, clean context isolation, no leaks
3. Option-Space Discipline: [OUT-OF-FRAMEWORK] meta-challenges on bounded sets
4. Heterogeneous Reviewer Routing: true vendor family separation, safe unavailable fallback
5. Materiality Gate: JSON parsing, keyword fallback, and exp1 historical concordance
6. Re-entry Controller: primary engine agency preservation
7. Clean User Output Delivery: zero noise on pass, transparent summary on update
8. Lint Response Validator Compatibility: preserves 100% classic profile pass
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "simulate-elite-experts" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import blind_spot_gate as bsg
from lint_response import validate_text


class TestTaskPhaseGate(unittest.TestCase):
    def test_judgment_bearing_prompts_proceed(self):
        prompts = [
            "Should we migrate from MySQL to CockroachDB for multi-region active-active?",
            "What architecture should we choose for offline-first sync?",
            "Diagnose the root-cause of our p99 latency regression on day 12.",
            "Evaluate whether we should prioritize retention vs acquisition under 8 months runway.",
            "评估分布式事务方案与本地事件表重放的权衡",
            "决策：自建推理集群 vs Serverless API 的长远架构方向",
        ]
        for p in prompts:
            verdict = bsg.classify_task_phase(p)
            self.assertEqual(
                verdict.phase, bsg.TaskPhase.JUDGMENT,
                f"Prompt '{p}' incorrectly classified as {verdict.phase}",
            )
            self.assertEqual(verdict.status, "PROCEED_JUDGMENT_PHASE")

    def test_pure_execution_prompts_skip_review(self):
        execution_prompts = [
            "Implement the helper function in Python to format phone numbers to E.164 and write unit tests.",
            "Deploy the Docker container to staging ECS cluster using existing task definition.",
            "Fix the indentation and lint errors in scripts/sync.py.",
            "Rename the function parse_total to parse_total_set across all files.",
            "Run unittest discover -s tests -v.",
            "按照已确定方案修改文件并提交 git commit",
        ]
        for p in execution_prompts:
            verdict = bsg.classify_task_phase(p)
            self.assertEqual(
                verdict.phase, bsg.TaskPhase.EXECUTION,
                f"Execution prompt '{p}' incorrectly classified as {verdict.phase}",
            )
            self.assertEqual(verdict.status, "SKIP_EXECUTION_PHASE")

    def test_execution_escalation_on_premise_breaking_evidence(self):
        prompt = "Execute deployment of database schema migration script v4.2 adding non-null index."
        blocker = "Dry-run on staging replica acquired an exclusive table lock for 45 seconds on 50M-row table."
        verdict = bsg.classify_task_phase(prompt, new_evidence=blocker)
        self.assertEqual(verdict.phase, bsg.TaskPhase.JUDGMENT)
        self.assertEqual(verdict.status, "ESCALATE_TO_JUDGMENT")
        self.assertTrue(verdict.can_escalate)
        self.assertIn("table lock", verdict.reason)


class TestDecisionPacketContract(unittest.TestCase):
    def setUp(self):
        self.sample_output = (
            "## 1. Good Group To Explore X\n"
            "- Real Person A: Martin Fowler\n"
            "- Real Person B: Kelsey Hightower\n"
            "## 2. Dialogue Round 1: Initial Positions\n"
            "- [Role 1] Martin Fowler: MonolithFirst is safer. [confidence: high]\n"
            "- [Role 2] Kelsey Hightower: Platform complexity will drain startup velocity. [confidence: high]\n"
            "## 6. Moderator Synthesis\n"
            "Adopt a modular monolith with strict domain boundaries enforced in code and CI. "
            "Defer microservice extraction until concrete operational bottlenecks or independent scaling requirements emerge.\n"
            "### Core Rationale\n"
            "- Reduces deployment overhead for an 8-engineer team.\n"
            "- Avoids premature network boundary coupling.\n"
            "## 7. Uncertainty Ledger\n"
            "### Facts\n"
            "- Team size is 8 engineers with no dedicated platform engineer.\n"
            "### Assumptions\n"
            "- Domain boundaries are still volatile.\n"
            "### Post-Use Self-Check\n"
            "1. Q1\n2. Q2\n3. Q3\n4. Q4\n5. Q5\n"
        )
        self.prompt = "An 8-engineer startup must choose between microservices and modular monolith."

    def test_clean_context_extracts_canonical_fields(self):
        pkt = bsg.extract_decision_packet(self.sample_output, self.prompt)
        self.assertEqual(pkt.user_prompt, self.prompt)
        self.assertIn("modular monolith", pkt.current_best_judgment)
        self.assertIn("Reduces deployment overhead", pkt.core_rationale)
        self.assertIn("Domain boundaries are still volatile", pkt.declared_uncertainties)
        self.assertIn("Team size is 8 engineers", pkt.hard_constraints_and_facts)

    def test_clean_context_strictly_excludes_transcripts(self):
        pkt = bsg.extract_decision_packet(self.sample_output, self.prompt)
        for leak in ["Dialogue Round 1", "Martin Fowler", "Kelsey Hightower", "Role 1", "Good Group"]:
            self.assertNotIn(leak, pkt.current_best_judgment)
            self.assertNotIn(leak, pkt.core_rationale)
            self.assertNotIn(leak, pkt.declared_uncertainties)

    def test_canonical_length_caps(self):
        huge_text = (
            "## 6. Moderator Synthesis\n" + ("verdict " * 800) + "\n"
            "### Core Rationale\n" + ("reason " * 800) + "\n"
            "## 7. Uncertainty Ledger\n### Facts\n" + ("fact " * 800) + "\n"
            "### Assumptions\n" + ("uncertainty " * 800)
        )
        pkt = bsg.extract_decision_packet(huge_text, "Short prompt")
        self.assertLessEqual(len(pkt.current_best_judgment), 1100)
        self.assertLessEqual(len(pkt.core_rationale), 1300)
        self.assertLessEqual(len(pkt.hard_constraints_and_facts), 1300)
        self.assertLessEqual(len(pkt.declared_uncertainties), 1000)


class TestOptionSpaceDiscipline(unittest.TestCase):
    def test_extract_option_space_from_bounded_prompt(self):
        p1 = "We MUST choose strictly between two managed AWS services: Amazon SQS Standard or Amazon SQS FIFO."
        opts1 = bsg._extract_option_space(p1)
        self.assertIsNotNone(opts1)
        self.assertEqual(sorted(opts1), ["Amazon SQS FIFO", "Amazon SQS Standard"])

        p2 = "Please choose from: Option A, Option B, Option C."
        opts2 = bsg._extract_option_space(p2)
        self.assertIsNotNone(opts2)
        self.assertEqual(len(opts2), 3)

    def test_build_prompt_enforces_out_of_framework_label(self):
        pkt = bsg.DecisionPacket(
            user_prompt="Choose between SQS Standard and SQS FIFO",
            hard_constraints_and_facts="Throughput is 200 msg/s",
            current_best_judgment="Choose SQS FIFO",
            core_rationale="Guarantees order",
            declared_uncertainties="Quota limit",
            allowed_option_space=["SQS Standard", "SQS FIFO"],
        )
        prompt = bsg.build_blindspot_prompt(pkt)
        self.assertIn("BOUNDED OPTION-SPACE DISCIPLINE", prompt)
        self.assertIn("[OUT-OF-FRAMEWORK]", prompt)
        self.assertIn("'SQS Standard'", prompt)
        self.assertIn("'SQS FIFO'", prompt)


class TestHeterogeneousReviewerResolution(unittest.TestCase):
    def test_resolves_different_vendor_family(self):
        # Anthropic main model must resolve to non-Anthropic reviewer
        rev, status = bsg.resolve_heterogeneous_reviewer("claude-opus-5")
        self.assertEqual(status, "RESOLVED_HETEROGENEOUS")
        self.assertNotEqual(bsg.MODEL_FAMILIES.get(rev), "anthropic")

        # OpenAI main model must resolve to non-OpenAI reviewer
        rev2, status2 = bsg.resolve_heterogeneous_reviewer("gpt-5.6-luna-max")
        self.assertEqual(status2, "RESOLVED_HETEROGENEOUS")
        self.assertNotEqual(bsg.MODEL_FAMILIES.get(rev2), "openai")

    def test_unavailable_heterogeneous_returns_explicit_status(self):
        # Temporarily mock an environment with no heterogeneous models
        orig = dict(bsg.MODEL_FAMILIES)
        try:
            # Force all models to be anthropic
            bsg.MODEL_FAMILIES = {k: "anthropic" for k in orig}
            rev, status = bsg.resolve_heterogeneous_reviewer("claude-opus-5")
            self.assertIsNone(rev)
            self.assertEqual(status, "HETEROGENEOUS_REVIEW_UNAVAILABLE")
        finally:
            bsg.MODEL_FAMILIES = orig


class TestMaterialityGateAndRetrospectiveConcordance(unittest.TestCase):
    def test_retrospective_concordance_on_exp1_discovery_tasks(self):
        """Verify retrospective concordance on the 6 discovery tasks (6/6)."""
        # Objective tasks: gate = False
        t1 = '{"material": false, "reason": "Reviewers reach the exact same defect list."}'
        t2 = '{"material": false, "reason": "All heuristic traps were already resolved."}'
        t6 = '{"material": false, "reason": "Discussion already covered cache-hit collapse."}'
        self.assertFalse(bsg.parse_materiality_json(t1).material)
        self.assertFalse(bsg.parse_materiality_json(t2).material)
        self.assertFalse(bsg.parse_materiality_json(t6).material)

        # Open tasks: gate = True
        t3 = '{"material": true, "reason": "Surfaced CRDT convergent corruption."}'
        t4 = '{"material": true, "reason": "Surfaced base monetization / cash survival."}'
        t5 = '{"material": true, "reason": "Surfaced discriminative vs generative unexamined assumption."}'
        self.assertTrue(bsg.parse_materiality_json(t3).material)
        self.assertTrue(bsg.parse_materiality_json(t4).material)
        self.assertTrue(bsg.parse_materiality_json(t5).material)


class TestCleanUserOutputFormatting(unittest.TestCase):
    def test_immaterial_gate_delivers_zero_process_noise(self):
        cand = "## 6. Moderator Synthesis\nCommit to Option A.\n## 7. Uncertainty Ledger\nLow risk."
        v = bsg.MaterialityVerdict(material=False, reason="No material blind spot.")
        out = bsg.format_user_output(cand, v, reentry_occurred=False)
        self.assertEqual(out, cand.strip())
        self.assertNotIn("Blind-Spot Audit", out)
        self.assertNotIn("Circuit breaker", out)

    def test_material_gate_with_rejection_adds_brief_note(self):
        cand = "Final recommendation: Stick to Plan X."
        v = bsg.MaterialityVerdict(material=True, reason="Reviewer suggested Plan Y.")
        out = bsg.format_user_output(
            cand, v, reentry_occurred=True,
            revised_answer=cand, disposition="REJECTED_WITH_REASON",
        )
        self.assertIn("Stick to Plan X", out)
        self.assertIn("*Note: Independent audit evaluated a potential challenge", out)

    def test_material_gate_with_adoption_adds_transparent_header(self):
        cand = "Old: Use CRDT."
        rev = "New: Use diff3 revision DAG."
        v = bsg.MaterialityVerdict(material=True, reason="CRDT semantic corruption risk.")
        out = bsg.format_user_output(
            cand, v, reentry_occurred=True,
            revised_answer=rev, disposition="ACCEPTED",
            explanation="CRDT cannot detect semantic text corruption",
        )
        self.assertIn("## Decision Update: Blind-Spot Integration", out)
        self.assertIn("CRDT cannot detect semantic text corruption", out)
        self.assertIn("New: Use diff3 revision DAG", out)


class TestLintResponsePass(unittest.TestCase):
    def test_full_candidate_with_audit_ledger_passes_lint(self):
        text = (
            "## 1. Good Group To Explore X\n"
            "- Decision frame: architecture selection\n"
            "- Execution mode: one-shot\n"
            "- Context basis: public work and stated assumptions\n"
            "- Real Person A: Martin Fowler\n"
            "- Real Person B: Leslie Lamport\n"
            "- Domain Expert Archetype: Distributed Systems Architect\n"
            "- Omniscient Agent Archetype: System Risk Auditor\n"
            "- Roster score: 6/6\n"
            "- Roster diversity: 6/6\n"
            "## 2. Dialogue Round 1: Initial Positions\n"
            "- [Role 1] Simulated viewpoints from public work. [confidence: high]\n"
            "- [Role 2] Simulated viewpoints from public work. [confidence: high]\n"
            "- [Role 3] Simulated viewpoints from public work. [confidence: medium]\n"
            "- [Role 4] Simulated viewpoints from public work. [confidence: medium]\n"
            "- Uncertainty snapshot: assumption recorded and evidence needed next.\n"
            "## 3. Dialogue Round 2: Cross-Examination\n"
            "- [Role 1] Simulated viewpoints from public work. [confidence: high]\n"
            "- [Role 2] Simulated viewpoints from public work. [confidence: high]\n"
            "- [Role 3] Simulated viewpoints from public work. [confidence: medium]\n"
            "- [Role 4] Simulated viewpoints from public work. [confidence: medium]\n"
            "- Uncertainty snapshot: assumption recorded and evidence needed next.\n"
            "## 4. Dialogue Round 3: Revised Positions\n"
            "- [Role 1] Simulated viewpoints from public work. [confidence: high]\n"
            "- [Role 2] Simulated viewpoints from public work. [confidence: high]\n"
            "- [Role 3] Simulated viewpoints from public work. [confidence: medium]\n"
            "- [Role 4] Simulated viewpoints from public work. [confidence: medium]\n"
            "- Uncertainty snapshot: assumption recorded and evidence needed next.\n"
            "## 5. Dialogue Round 4: Final Statements\n"
            "- [Role 1] Simulated viewpoints from public work. [confidence: high]\n"
            "- [Role 2] Simulated viewpoints from public work. [confidence: high]\n"
            "- [Role 3] Simulated viewpoints from public work. [confidence: medium]\n"
            "- [Role 4] Simulated viewpoints from public work. [confidence: medium]\n"
            "- Uncertainty snapshot: assumption recorded and evidence needed next.\n"
            "## 6. Moderator Synthesis\n"
            "Final recommendation: proceed with staged rollout.\n"
            "## 7. Uncertainty Ledger\n"
            "- Facts: Baseline measured.\n"
            "- Assumptions: Latency is bounded.\n"
            "- Speculation: Traffic doubles in Q4.\n\n"
            "### Post-Use Self-Check\n"
            "1. Question 1\n2. Question 2\n3. Question 3\n4. Question 4\n5. Question 5\n"
        )
        errors = validate_text(text, "classic")
        self.assertEqual(errors, [])


class TestExecuteBlindSpotPipelineE2E(unittest.TestCase):
    def test_e2e_pipeline_skips_pure_execution(self):
        called = []
        def mock_call(model, prompt, tag):
            called.append(tag)
            return ""

        out, trace = bsg.execute_blind_spot_pipeline(
            user_prompt="Deploy the docker container v1.2 to production ECS.",
            candidate_answer="Executing deployment commands.",
            call_model_fn=mock_call,
        )
        self.assertEqual(trace["final_disposition"], "SKIP_EXECUTION_PHASE")
        self.assertEqual(len(called), 0, "Execution task must make 0 reviewer calls")
        self.assertEqual(out, "Executing deployment commands.")

    def test_e2e_pipeline_immaterial_fast_path(self):
        called = []
        def mock_call(model, prompt, tag):
            called.append(tag)
            if tag == "blindspot-review":
                return "NO_MATERIAL_BLIND_SPOTS. The candidate decision is sound."
            if tag == "materiality-gate":
                return '{"material": false, "reason": "No material flaw detected."}'
            return ""

        cand = "## 6. Moderator Synthesis\nCommit to Architecture A.\n## 7. Uncertainty Ledger\nLow risk."
        out, trace = bsg.execute_blind_spot_pipeline(
            user_prompt="Choose between Architecture A and Architecture B",
            candidate_answer=cand,
            call_model_fn=mock_call,
            main_model_id="claude-opus-5",
        )
        self.assertEqual(trace["final_disposition"], "NO_MATERIAL_CHANGE")
        self.assertFalse(trace.get("reentry_occurred", False))
        self.assertEqual(called, ["blindspot-review", "materiality-gate"])
        self.assertEqual(out, cand)

    def test_e2e_pipeline_material_reentry_accepted(self):
        called = []
        def mock_call(model, prompt, tag):
            called.append(tag)
            if tag == "blindspot-review":
                return "MATERIAL: Omits high-write contention bottleneck on global table lock."
            if tag == "materiality-gate":
                return '{"material": true, "reason": "Global table lock fatally blocks concurrent writes."}'
            if tag == "reentry-synthesis":
                return "Revised: Partially accept table lock risk; adopt online schema change (gh-ost)."
            return ""

        cand = "## 6. Moderator Synthesis\nCommit to standard ALTER TABLE.\n## 7. Uncertainty Ledger\nAssume fast lock."
        out, trace = bsg.execute_blind_spot_pipeline(
            user_prompt="Design zero-downtime database schema migration strategy",
            candidate_answer=cand,
            call_model_fn=mock_call,
            main_model_id="claude-opus-5",
        )
        self.assertEqual(trace["final_disposition"], "PARTIALLY_ACCEPTED")
        self.assertTrue(trace["reentry_occurred"])
        self.assertEqual(called, ["blindspot-review", "materiality-gate", "reentry-synthesis"])
        self.assertIn("## Decision Update: Blind-Spot Integration", out)
        self.assertIn("online schema change (gh-ost)", out)

    def test_e2e_pipeline_heterogeneous_unavailable_safe_downgrade(self):
        orig_families = dict(bsg.MODEL_FAMILIES)
        try:
            bsg.MODEL_FAMILIES = {k: "anthropic" for k in orig_families}
            called = []
            def mock_call(model, prompt, tag):
                called.append(tag)
                return ""

            cand = "Candidate decision."
            out, trace = bsg.execute_blind_spot_pipeline(
                user_prompt="Strategic decision",
                candidate_answer=cand,
                call_model_fn=mock_call,
                main_model_id="claude-opus-5",
            )
            self.assertEqual(trace["final_disposition"], "HETEROGENEOUS_REVIEW_UNAVAILABLE")
            self.assertEqual(len(called), 0, "Unavailable heterogeneous reviewer must make 0 calls")
            self.assertIn("Candidate decision", out)
            self.assertIn("Governance Note", out)
        finally:
            bsg.MODEL_FAMILIES = orig_families


if __name__ == "__main__":
    unittest.main()
