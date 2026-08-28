#!/usr/bin/env python3
"""test_autonomy_contract.py — AUTONOMOUS_INTENT_TO_COMPLETION 语义固化回归。

把统一自主执行契约的关键语义锚点固化为文本断言：
- canonical 归属（clarify-before-change 管"问不问"，execution-discipline 管"停不停/怎么恢复"）；
- 已消除的冲突表述不得回归（如"用户逐条确认"、"3 次失败必须停止"、"信息不足立即暂停询问"）；
- 各执行类 Skill 必须引用共同语义（Progress Delta / 合法停止策略 / DEADBAND / 事件驱动验证）。

只读仓库文件，不触碰任何运行态数据。
"""
from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).parent.parent
SKILLS = REPO / "skills"


def read_skill(name: str, *parts: str) -> str:
    path = SKILLS.joinpath(name, *parts)
    return path.read_text(encoding="utf-8")


class TestClarifyCanonical(unittest.TestCase):
    """clarify-before-change 是"是否需要询问用户"的 canonical 语义所有者。"""

    def setUp(self):
        self.text = read_skill("clarify-before-change", "SKILL.md")

    def test_intent_resolution_policy_present(self):
        for anchor in ("意图解析策略", "自主解析顺序", "可逆假设", "允许询问"):
            self.assertIn(anchor, self.text)

    def test_continuation_semantics_present(self):
        self.assertIn("续接语义", self.text)
        self.assertIn("继续", self.text)

    def test_ask_boundary_kept_narrow(self):
        self.assertIn("只有答案会", self.text)  # 原有更窄询问边界保留


class TestExecutionDisciplineCanonical(unittest.TestCase):
    """execution-discipline 承载 Progress/Recovery/Stop 的 canonical 语义。"""

    def setUp(self):
        self.text = read_skill("execution-discipline", "SKILL.md")

    def test_progress_delta_present(self):
        for anchor in ("PROGRESS_DELTA", "DONE_CRITERION_CLOSED", "MEANINGFUL_MUTATION",
                       "BLOCKER_REMOVED", "DECISION_RESOLVED", "NEW_ACTION_CHANGING_EVIDENCE"):
            self.assertIn(anchor, self.text)

    def test_no_progress_governor_present(self):
        self.assertIn("NO_PROGRESS_STREAK", self.text)
        self.assertIn("WHAT_CHANGED", self.text)
        self.assertIn("Distance-to-Done", self.text)

    def test_evidence_sufficiency_present(self):
        self.assertIn("EVIDENCE_SUFFICIENT", self.text)

    def test_recovery_ladder_with_mandatory_resume(self):
        self.assertIn("Recovery Ladder", self.text)
        self.assertIn("RESUME ORIGINAL GOAL", self.text)
        self.assertIn("RECOVERY_WITHOUT_RESUME", self.text)

    def test_valid_stop_policy_and_invalid_reasons(self):
        self.assertIn("合法停止策略", self.text)
        for anchor in ("DONE", "TRUE_HUMAN_JUDGMENT_REQUIRED",
                       "IRREVERSIBLE_OR_HIGH_RISK_AUTHORIZATION_REQUIRED",
                       "EXTERNAL_CAPABILITY_BLOCKER", "VERIFIED_EXHAUSTION"):
            self.assertIn(anchor, self.text)
        self.assertIn("Invalid Stop Reasons", self.text)

    def test_context_anxiety_not_a_stop(self):
        self.assertIn("CONTEXT_ANXIETY", self.text)

    def test_rule_precedence_present(self):
        self.assertIn("EXECUTION_RULE_PRECEDENCE", self.text)


class TestUnifiedTaskflowAligned(unittest.TestCase):
    """unified-taskflow 与 canonical 语义对齐：Phase 0 / 3-Strike / RIPER #4。"""

    def setUp(self):
        self.skill = read_skill("unified-taskflow", "SKILL.md")
        self.phase0 = read_skill("unified-taskflow", "references", "phase0-clarification.md")
        self.gov = read_skill("unified-taskflow", "references", "governance.md")
        self.anchor = read_skill("unified-taskflow", "assets", "templates", "anchor.md")

    def test_no_per_item_user_confirmation(self):
        # 冲突 A 的旧表述不得回归
        for text in (self.skill, self.phase0):
            self.assertNotIn("用户逐条确认", text)
        self.assertNotIn("请确认是否正确", self.phase0)

    def test_reversible_assumption_proceeds(self):
        self.assertIn("可逆", self.skill)
        self.assertIn("记录即推进", self.phase0)
        self.assertIn("可逆", self.anchor)

    def test_three_strike_reinterpreted_as_deadband(self):
        self.assertIn("DEADBAND_TRIPPED", self.skill)
        self.assertIn("不得自动", self.skill)
        # 冲突 B 的旧表述不得回归
        self.assertNotIn("停止尝试**，升级给用户", self.skill)
        self.assertNotIn("3 次失败必须停止", self.gov)

    def test_riper_pause_removed(self):
        self.assertNotIn("信息不足立即暂停询问", self.skill)

    def test_continuation_semantics_linked(self):
        self.assertIn("续接语义", self.skill)


class TestDecisionGatesEventDriven(unittest.TestCase):
    """decision-gates：固定周期审计改事件驱动 + 验证预算。"""

    def setUp(self):
        self.text = read_skill("decision-gates", "SKILL.md")

    def test_gate1_event_driven(self):
        self.assertIn("事件驱动", self.text)
        self.assertNotIn("每 3 个 checkpoint 启动", self.text)
        self.assertIn("最终验收（必跑）", self.text)

    def test_verification_purpose_present(self):
        self.assertIn("VERIFICATION_PURPOSE", self.text)
        for anchor in ("hypothesis", "uncertainty_removed", "pass_action", "fail_action"):
            self.assertIn(anchor, self.text)

    def test_zero_token_gates_kept(self):
        # 安全门禁不得因事件化被删除
        for anchor in ("gate_scope_lock", "gate_deadband", "闸门 2", "闸门 3"):
            self.assertIn(anchor, self.text)


class TestSubagentGovernanceBlockedGate(unittest.TestCase):
    """subagent-execution-governance：BLOCKED 有效性门禁 + orchestrator 解决阶梯。"""

    def setUp(self):
        self.text = read_skill("subagent-execution-governance", "SKILL.md")

    def test_blocked_validity_gate_fields(self):
        for anchor in ("BLOCKED_INVALID", "blocked_reason", "why_it_blocks_done",
                       "recovery_steps_attempted", "alternative_routes_attempted",
                       "what_external_fact_is_missing", "why_agent_cannot_obtain_it"):
            self.assertIn(anchor, self.text)

    def test_orchestrator_ladder_before_user(self):
        self.assertIn("不得直接转发用户", self.text)
        self.assertIn("合法停止策略", self.text)

    def test_no_indefinite_researching(self):
        self.assertIn("STILL_RESEARCHING", self.text)
        self.assertIn("EVIDENCE_SUFFICIENT", self.text)


class TestTaskModeRouterBias(unittest.TestCase):
    """task-mode-router：低风险可逆动作偏好。"""

    def test_reversible_action_bias_present(self):
        text = read_skill("task-mode-router", "SKILL.md")
        self.assertIn("低风险可逆动作偏好", text)
        self.assertIn("LOW-RISK REVERSIBLE ACTION BIAS", text)


if __name__ == "__main__":
    unittest.main()
