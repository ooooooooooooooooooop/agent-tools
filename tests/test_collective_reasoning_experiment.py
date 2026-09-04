"""Unit tests for the collective-reasoning experiment harness (pure functions only)."""

from __future__ import annotations

import unittest
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "experiments" / "collective_reasoning"

import sys

sys.path.insert(0, str(EXPERIMENT_DIR))

import client  # noqa: E402
import judge  # noqa: E402
import tasks as tasks_mod  # noqa: E402
from verify_objective import check_t1, check_t2, check_t6  # noqa: E402


class TestVerifyObjective(unittest.TestCase):
    def test_t1_detects_all_defects(self):
        text = (
            "R1 is violated: with half-open intervals, two ranges that touch "
            "(end equals start) are not merged because the code uses `r[0] > merged[-1][1]` "
            "instead of `>=`. R2: empty input returns None instead of []. "
            "R3: ranges.sort() mutates the caller's input list in place. "
            "R4: the output contains lists instead of tuples, and 3-tuple tier inputs "
            "are not normalized away. R5: start > end is never rejected with ValueError."
        )
        res = check_t1(text)
        self.assertEqual(res["defects_detected"], 5, res["detail"])
        for defect, info in res["detail"].items():
            self.assertTrue(info["detected"], defect)

    def test_t1_no_false_positive_on_clean_description(self):
        text = "The function looks correct overall. I would add docstring examples and type hints."
        res = check_t1(text)
        self.assertLessEqual(res["defects_detected"], 2, res["detail"])

    def test_t2_parses_total_and_set(self):
        good = "Blah blah.\nTOTAL=18; SET=B,C,D\nConfidence: high"
        res = check_t2(good)
        self.assertTrue(res["correct"])
        self.assertEqual(res["parsed_total"], 18)
        self.assertEqual(res["parsed_set"], ["B", "C", "D"])

    def test_t2_rejects_wrong_total(self):
        res = check_t2("TOTAL=14; SET=A,D")
        self.assertFalse(res["correct"])
        self.assertEqual(res["parsed_total"], 14)

    def test_t2_parses_rich_rendered_style(self):
        """COLLECTIVE's rendered output is prose ('Chosen Set: {B, C, D}',
        'Total Value: 18'), not the literal 'TOTAL=18; SET=B,C,D' contract.
        The checker must not misparse the objectively-correct 18 as a wrong
        total because a lower suboptimal figure (value-greedy = 14) appears
        earlier in the document."""
        text = (
            "## Analysis\nSome reasoning: value-greedy picks A and reaches only "
            "a suboptimal total of 14; earliest-ending-first gets 16.\n"
            "## Final Judgment\n* **Chosen Set:** ${B, C, D}$\n"
            "* **Total Value:** 18\n## Confidence\nhigh"
        )
        res = check_t2(text)
        self.assertTrue(res["correct"], res)
        self.assertEqual(res["parsed_total"], 18)
        self.assertEqual(res["parsed_set"], ["B", "C", "D"])

    def test_t2_greedy_trap_detection(self):
        res = check_t2("A value-greedy approach would wrongly include session A (value 10) and reach only 14.")
        self.assertTrue(res["greedy_trap_addressed"])

    def test_t6_deploy_verdict(self):
        text = "PRIMARY CAUSE: cache hit rate collapsed from ~92% to ~54% on day 12\nDAY-12 DEPLOY: not causally implicated; day-3 deploy caused no regression\nSECONDARY: traffic growth"
        res = check_t6(text)
        self.assertTrue(res["primary_is_cache"])
        self.assertEqual(res["deploy_verdict"], "not_causally_implicated")
        self.assertTrue(res["correct"])

    def test_t6_wrong_diagnosis(self):
        text = "PRIMARY CAUSE: the day-12 deploy introduced a regression\nDAY-12 DEPLOY: causally implicated\n"
        res = check_t6(text)
        self.assertFalse(res["correct"])


class TestAnonymization(unittest.TestCase):
    def test_scrub_removes_vendor_names(self):
        text = "Claude Sonnet and Gemini Flash from Google; GPT from OpenAI; Kimi k3."
        out = judge.scrub(text)
        for word in ("claude", "sonnet", "gemini", "google", "gpt", "openai", "kimi"):
            self.assertNotIn(word, out.lower())

    def test_extract_generic_contract(self):
        text = "## Analysis\nSome reasoning.\n## Final Judgment\nPick option A.\n## Confidence\nmedium because x"
        doc = judge.extract_decision_doc(tasks_mod.TASKS[0], text, "initial")
        self.assertIn("Pick option A.", doc)
        self.assertIn("Confidence: medium because x", doc)

    def test_extract_current_synthesis(self):
        text = (
            "## 1. Good Group To Explore X (Four-Lens Roster)\nroster\n"
            "## 6. Moderator Synthesis\nThe recommendation is X.\n"
            "## 7. Uncertainty Ledger\n- fact: something\n"
            "### Post-Use Self-Check\n1. q\n2. q\n3. q\n4. q\n5. q"
        )
        doc = judge.extract_decision_doc(tasks_mod.TASKS[0], text, "CURRENT")
        self.assertIn("The recommendation is X.", doc)
        self.assertNotIn("Roster", doc)
        self.assertNotIn("Post-Use Self-Check", doc)


class TestClient(unittest.TestCase):
    def test_call_key_deterministic(self):
        msgs = [{"role": "user", "content": "hi"}]
        k1 = client.call_key("run1", "tag", "glm-5.3", msgs, 8000)
        k2 = client.call_key("run1", "tag", "glm-5.3", msgs, 8000)
        k3 = client.call_key("run1", "tag2", "glm-5.3", msgs, 8000)
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_roster_families_distinct(self):
        fams = [client.MODELS[a]["family"] for a in client.PARTICIPANTS]
        self.assertEqual(len(set(fams)), len(fams))
        for j in client.JUDGES:
            self.assertNotIn(j, client.PARTICIPANTS)


class TestTaskContracts(unittest.TestCase):
    def test_all_tasks_have_contract(self):
        for task in tasks_mod.TASKS:
            p = tasks_mod.task_user_prompt(task)
            self.assertIn("## Final Judgment", p)
            self.assertIn("## Confidence", p)

    def test_objective_tasks_have_ground_truth(self):
        for tid in ("T1", "T2", "T6"):
            self.assertTrue(tasks_mod.TASKS_BY_ID[tid].ground_truth)

    def test_t2_ground_truth_consistent(self):
        gt = tasks_mod.TASKS_BY_ID["T2"].ground_truth
        sessions = gt["sessions"]
        best = None
        from itertools import combinations

        names = list(sessions)
        for r in range(1, len(names) + 1):
            for combo in combinations(names, r):
                ok = True
                for a, b in combinations(combo, 2):
                    s1, e1, _ = sessions[a]
                    s2, e2, _ = sessions[b]
                    if s1 < e2 and s2 < e1:
                        ok = False
                        break
                if ok:
                    total = sum(sessions[c][2] for c in combo)
                    if best is None or total > best:
                        best = total
        self.assertEqual(best, gt["optimum_total"])


if __name__ == "__main__":
    unittest.main()
