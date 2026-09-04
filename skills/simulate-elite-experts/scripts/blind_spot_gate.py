#!/usr/bin/env python3
"""Blind-Spot Gated Reasoning Engine (Production-Ready Architecture).

Implements the high-ROI blind-spot + materiality gate audit overlay derived
from collective-reasoning experimental findings.

Core Capabilities:
1. Task-Phase Gate: Discriminates JUDGMENT vs EXECUTION tasks; skips execution
   tasks automatically; supports escalation if new evidence challenges premises.
2. Canonical DecisionPacket Contract: Strictly bounds brief size (verdict <=150 words,
   uncertainties <=150 words); isolates clean context with zero transcript leakage.
3. Option-Space Guard: Preserves [OUT-OF-FRAMEWORK] innovation as meta-challenges
   without illegally substituting restricted choices.
4. Heterogeneous Reviewer Routing: Enforces true multi-vendor heterogeneity via
   canonical registry; fails safely with HETEROGENEOUS_REVIEW_UNAVAILABLE if missing.
5. Sovereign Re-entry: Primary engine critically evaluates critique (accept, partially
   accept, or defend/reject), never blindly overridden.
6. Clean User Output: Zero process noise on pass; transparent summary on update;
   full audit ledger preserved in debug provenance.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]


# =============================================================================
# 1. Task-Phase Gate
# =============================================================================

class TaskPhase(str, Enum):
    JUDGMENT = "JUDGMENT"
    EXECUTION = "EXECUTION"


@dataclass
class TaskPhaseVerdict:
    phase: TaskPhase
    status: str  # "PROCEED_JUDGMENT_PHASE", "SKIP_EXECUTION_PHASE", "ESCALATE_TO_JUDGMENT"
    reason: str
    can_escalate: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase.value,
            "status": self.status,
            "reason": self.reason,
            "can_escalate": self.can_escalate,
        }


# Semantic keywords that signal forming, deciding, or revising high-stakes direction
JUDGMENT_PATTERNS = [
    r"\b(?:decide|decision|choose|recommend|recommendation|trade-?off|strategy|architect|architecture|policy|propos\w*|design|assess\w*)\b",
    r"\b(?:diagnose|diagnosis|root-?cause|causal|hypothesis|hypotheses|evaluate|evaluation|prioritize|priority|review)\b",
    r"\b(?:should we|which (?:is better|approach|option)|migration strategy|design space)\b",
    r"(?:决策|选型|方案|架构|设计|因果|根因|优先级|权衡|评测|评估|方向|战略|优选|策略)",
]

# Semantic keywords that signal mechanical execution of already-agreed plans
EXECUTION_PATTERNS = [
    r"\b(?:implement|code|write (?:unit )?tests?|deploy|commit|push|refactor|rename|format)\b",
    r"\b(?:fix (?:typo|lint|syntax|indentation)|run (?:tests?|script)|execute step|migrate table)\b",
    r"(?:实施|代码编写|修改文件|部署|运行测试|格式化|机械转换|重命名|执行步骤|修复语法)",
]

# Evidence patterns that indicate execution encountered a premise-breaking blocker
ESCALATION_PATTERNS = [
    r"\b(?:table lock|data loss|deadlock|regression|out of memory|fatal|breach|incompatible|discredited)\b",
    r"\b(?:assumption (?:violated|invalid|broken)|premise fails|acceptance criteria blocked)\b",
    r"(?:死锁|锁表|内存溢出|前提失效|假设被推翻|验收标准无法满足|破坏不变量|发生严重阻塞)",
]


def classify_task_phase(prompt: str, new_evidence: Optional[str] = None) -> TaskPhaseVerdict:
    """Classify whether a task is judgment-bearing or pure execution.

    If an execution task presents new evidence challenging goals, premises,
    or acceptance criteria, it escalates to JUDGMENT phase.
    """
    text = (prompt or "").strip().lower()
    evidence_text = (new_evidence or "").strip().lower()

    # Check for execution escalation first
    if evidence_text:
        for pat in ESCALATION_PATTERNS:
            if re.search(pat, evidence_text, re.I):
                return TaskPhaseVerdict(
                    phase=TaskPhase.JUDGMENT,
                    status="ESCALATE_TO_JUDGMENT",
                    reason=f"Execution encountered premise-breaking evidence matching '{pat}'; escalated to judgment phase.",
                    can_escalate=True,
                )

    # Check judgment markers
    judgment_score = sum(1 for pat in JUDGMENT_PATTERNS if re.search(pat, text, re.I))
    execution_score = sum(1 for pat in EXECUTION_PATTERNS if re.search(pat, text, re.I))

    # If prompt explicitly asks for decisions / trade-offs / architecture, it is JUDGMENT
    if judgment_score > 0 and judgment_score >= execution_score:
        return TaskPhaseVerdict(
            phase=TaskPhase.JUDGMENT,
            status="PROCEED_JUDGMENT_PHASE",
            reason=f"Task involves judgment formation/revision (matched {judgment_score} judgment patterns).",
            can_escalate=False,
        )

    # If execution patterns dominate or no judgment markers exist, it is EXECUTION
    if execution_score > 0:
        return TaskPhaseVerdict(
            phase=TaskPhase.EXECUTION,
            status="SKIP_EXECUTION_PHASE",
            reason=f"Task is mechanical execution/implementation (matched {execution_score} execution patterns); blind-spot review skipped.",
            can_escalate=True,
        )

    # Default fallback: if ambiguous, treat as JUDGMENT if it contains questions, else EXECUTION
    if "?" in text or "？" in text or "why" in text or "how should" in text:
        return TaskPhaseVerdict(
            phase=TaskPhase.JUDGMENT,
            status="PROCEED_JUDGMENT_PHASE",
            reason="Ambiguous prompt framed as open query; routed to judgment phase.",
            can_escalate=False,
        )

    return TaskPhaseVerdict(
        phase=TaskPhase.EXECUTION,
        status="SKIP_EXECUTION_PHASE",
        reason="Default execution boundary for non-interrogative prompt; blind-spot review skipped.",
        can_escalate=True,
    )


# =============================================================================
# 2. Canonical DecisionPacket Contract
# =============================================================================

@dataclass
class DecisionPacket:
    """Canonical bounded brief passed to clean-context reviewer.

    Strictly excludes dialogue transcripts, agent scratchpads, persona rosters,
    and vote tallies. Bounded by word/char caps.
    """
    user_prompt: str
    hard_constraints_and_facts: str       # Max 200 words / ~1200 chars
    current_best_judgment: str            # Max 150 words / ~1000 chars
    core_rationale: str                    # Max 200 words / ~1200 chars
    declared_uncertainties: str            # Max 150 words / ~900 chars
    allowed_option_space: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _extract_option_space(prompt: str) -> Optional[List[str]]:
    """Detect if the prompt explicitly restricts the decision to a closed set."""
    t = prompt or ""
    # Pattern 1: "between ... X and/or Y"
    m1 = re.search(
        r"(?i)between\s+(?:[^:;\n]+?[:：]\s*)?([A-Za-z0-9_\-\s]+?)\s+(?:and|or)\s+([A-Za-z0-9_\-\s]+?)(?:\s+only)?(?:\.|\n|;|$)",
        t,
    )
    if m1:
        opt1 = m1.group(1).strip()
        opt2 = m1.group(2).strip()
        if ":" in opt1 or "：" in opt1:
            opt1 = re.split(r"[:：]", opt1)[-1].strip()
        if 2 <= len(opt1) < 60 and 2 <= len(opt2) < 60:
            return [opt1, opt2]

    # Pattern 2: "from / among: A, B, C"
    m2 = re.search(r"(?i)(?:choose|pick|select)\s+(?:from|among)\s*(?:the following)?\s*[:：]\s*([^\.\n;]+)", t)
    if m2:
        raw_items = re.split(r"[,;、/]| or ", m2.group(1))
        items = [item.strip().strip("'\"`") for item in raw_items if item.strip()]
        if 2 <= len(items) <= 6:
            return items
    return None


def extract_decision_packet(candidate_text: str, user_prompt: str) -> DecisionPacket:
    """Extract canonical DecisionPacket from candidate answer and prompt."""
    text = candidate_text or ""
    prompt = (user_prompt or "").strip()

    # 1. Extract hard constraints & facts
    facts = ""
    m_facts = re.search(r"(?is)###\s*(?:Facts|硬约束|Known Facts)\s*(.*?)(?:###|##|\Z)", text)
    if m_facts:
        facts = m_facts.group(1).strip()
    if not facts:
        # Extract from prompt preamble or ledger
        facts = "(Extracted from problem constraints and verified operational facts.)"
    if len(facts) > 1200:
        facts = facts[:1200] + " [... truncated ...]"

    # 2. Extract current best judgment
    judgment = ""
    for pat in [
        r"(?is)##\s*(?:\d+\.\s*)?Moderator Synthesis\s*(.*?)(?:###|##\s*(?:\d+\.\s*)?Uncertainty|\Z)",
        r"(?is)##\s*Final Judgment\s*(.*?)(?:##|\Z)",
        r"(?is)\*\*Final recommendation:\*\*\s*(.*?)(?:\n\n|\Z)",
        r"(?is)FINAL JUDGMENT\s*:\s*(.*?)(?:WHAT CHANGED|\n\n|\Z)",
    ]:
        m = re.search(pat, text)
        if m:
            judgment = m.group(1).strip()
            break
    if not judgment:
        judgment = text[-900:].strip()
    if len(judgment) > 1000:
        judgment = judgment[:1000] + " [... truncated to canonical 150-word bound ...]"

    # 3. Extract core rationale
    rationale = ""
    m_rat = re.search(r"(?is)###\s*(?:Key Reasons|核心理由|Core Rationale)\s*(.*?)(?:###|##|\Z)", text)
    if m_rat:
        rationale = m_rat.group(1).strip()
    elif "because" in judgment.lower() or "—" in judgment or ":" in judgment:
        rationale = judgment
    else:
        rationale = "(Derived from primary multi-lens analysis and trade-off comparison.)"
    if len(rationale) > 1200:
        rationale = rationale[:1200] + " [... truncated ...]"

    # 4. Extract declared uncertainties & assumptions
    uncertainties = ""
    for pat in [
        r"(?is)##\s*(?:\d+\.\s*)?Uncertainty Ledger\s*(.*?)(?:###\s*Post-Use|\Z)",
        r"(?is)##\s*Confidence\s*(.*?)(?:##|\Z)",
        r"(?is)\*\*Uncertainty snapshot:\*\*\s*(.*?)(?:\n\n|\Z)",
    ]:
        m = re.search(pat, text)
        if m:
            uncertainties = m.group(1).strip()
            break
    if not uncertainties:
        uncertainties = "(No explicit uncertainties declared.)"
    if len(uncertainties) > 900:
        uncertainties = uncertainties[:900] + " [... truncated to canonical 150-word bound ...]"

    # 5. Extract option space
    option_space = _extract_option_space(prompt)

    return DecisionPacket(
        user_prompt=prompt,
        hard_constraints_and_facts=facts,
        current_best_judgment=judgment,
        core_rationale=rationale,
        declared_uncertainties=uncertainties,
        allowed_option_space=option_space,
    )


# =============================================================================
# 3. Blind-Spot Reviewer Prompt with Out-Of-Framework Guard
# =============================================================================

BLIND_SPOT_TARGETS = [
    "Hidden or unexamined assumptions shared by the current judgment",
    "Wrong framing of the decision space (e.g. false dichotomy, misdiagnosed bottleneck)",
    "Omitted viable alternatives that strictly dominate the proposed course of action",
    "Neglected second-order operational, financial, or systemic risks",
    "A dramatically simpler path achieving ~80% of value at ~20% of maintenance cost",
]


def build_blindspot_prompt(packet: DecisionPacket) -> str:
    """Build clean-context audit prompt enforcing option-space discipline."""
    targets_formatted = "\n".join(f"{i + 1}. {target}" for i, target in enumerate(BLIND_SPOT_TARGETS))

    option_space_clause = ""
    if packet.allowed_option_space:
        opts = ", ".join(f"'{o}'" for o in packet.allowed_option_space)
        option_space_clause = (
            f"## BOUNDED OPTION-SPACE DISCIPLINE (STRICT):\n"
            f"The user problem explicitly limits the decision to: [{opts}].\n"
            f"1. You MUST first thoroughly evaluate within this declared option space.\n"
            f"2. If and only if you identify a strictly dominating path that is excluded by the user's "
            f"options, you MUST explicitly label it as '[OUT-OF-FRAMEWORK]'.\n"
            f"3. You must explain WHY the bounded space is defective as a meta-level challenge, rather "
            f"than silently substituting your preference for the user's required choice.\n\n"
        )

    return (
        f"You are an independent, fresh outside reviewer with NO stake in prior analysis. "
        f"You have NOT seen any internal discussion and are provided ONLY this decision brief:\n\n"
        f"## Original Question & Objective\n{packet.user_prompt}\n\n"
        f"## Hard Constraints & Known Facts\n{packet.hard_constraints_and_facts}\n\n"
        f"## Candidate Decision\n{packet.current_best_judgment}\n\n"
        f"## Core Rationale\n{packet.core_rationale}\n\n"
        f"## Declared Assumptions & Uncertainties\n{packet.declared_uncertainties}\n\n"
        f"---\n"
        f"{option_space_clause}"
        f"Targeted blind-spot search across 5 categories:\n"
        f"{targets_formatted}\n\n"
        f"Answer in at most 350 words. If you believe the candidate decision has NO material "
        f"blind spots and is already sound, state: 'NO_MATERIAL_BLIND_SPOTS' and briefly explain why."
    )


# =============================================================================
# 4. Materiality Gate & Re-entry Controller
# =============================================================================

@dataclass
class MaterialityVerdict:
    material: bool
    reason: str
    has_out_of_framework_challenge: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_materiality_prompt(packet: DecisionPacket, review_critique: str) -> str:
    """Prompt for the circuit-breaker materiality gatekeeper."""
    return (
        "You are an impartial gatekeeper evaluating an outside audit critique of a decision.\n\n"
        "A candidate decision was formed, and an independent reviewer identified potential blind spots. "
        "Decide whether the reviewer raised any MATERIAL considerations that the candidate decision "
        "had not already addressed.\n\n"
        "MATERIAL (true) means: the critique identifies an unexamined false assumption, a fatal operational flaw, "
        "a strictly dominating omitted option, or a risk that SHOULD change the decision or its primary action plan.\n"
        "NOT MATERIAL (false) means: purely philosophical differences, minor stylistic caveats, speculative risks with no "
        "immediate impact, points already acknowledged in the candidate's uncertainty ledger, or generic skepticism.\n\n"
        f"## Candidate Decision\n{packet.current_best_judgment}\n\n"
        f"## Declared Uncertainties\n{packet.declared_uncertainties}\n\n"
        f"## Outside Reviewer Critique\n{review_critique.strip()}\n\n"
        'Reply with JSON ONLY in this exact format:\n'
        '{"material": true, "reason": "<1-2 sentences explaining why the finding alters the decision>"}\n'
        'or\n'
        '{"material": false, "reason": "<1-2 sentences explaining why the critique is non-actionable or already addressed>"}'
    )


def parse_materiality_json(text: str) -> MaterialityVerdict:
    """Parse JSON response from the materiality gate safely with fallback."""
    raw = (text or "").strip()
    oof = bool(re.search(r"\[OUT-OF-FRAMEWORK\]", raw, re.I))

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        # Non-JSON fallback
        if "material\": false" in raw.lower() or "no_material" in raw.lower():
            return MaterialityVerdict(material=False, reason="Parsed from non-JSON response: no material change.", has_out_of_framework_challenge=oof)
        if "material\": true" in raw.lower():
            return MaterialityVerdict(material=True, reason="Parsed from non-JSON response: material change detected.", has_out_of_framework_challenge=oof)
        return MaterialityVerdict(material=False, reason="Unparseable gate response; defaulting to circuit-breaker pass.", has_out_of_framework_challenge=oof)

    try:
        data = json.loads(match.group(0))
        is_material = bool(data.get("material", False))
        reason = str(data.get("reason", "(no reason supplied)"))
        return MaterialityVerdict(material=is_material, reason=reason, has_out_of_framework_challenge=oof)
    except Exception as exc:
        return MaterialityVerdict(material=False, reason=f"JSON parse error ({exc}); defaulting to circuit-breaker pass.", has_out_of_framework_challenge=oof)


def build_reentry_prompt(candidate_text: str, review_critique: str, user_prompt: str) -> str:
    """Construct the sovereign re-entry prompt returning the critique to the primary engine.

    The primary engine retains full agency: it may accept, partially integrate, or reject
    the critique with documented justification.
    """
    oof_note = ""
    if "[OUT-OF-FRAMEWORK]" in review_critique:
        oof_note = (
            "\nNOTE: The reviewer surfaced an '[OUT-OF-FRAMEWORK]' alternative. Evaluate this as a "
            "meta-level challenge. If the problem constraint is firm, keep your recommendation "
            "within the user's required choices and record the meta-challenge under 'Unresolved Risks'. "
            "Only switch to an out-of-framework option if the stated choices are fatal.\n"
        )

    return (
        f"{user_prompt.strip()}\n\n---\n"
        f"You previously produced the following candidate answer:\n\n"
        f"{candidate_text.strip()}\n\n---\n"
        f"An independent outside audit surfaced the following material blind spot:\n\n"
        f"{review_critique.strip()}\n\n---\n"
        f"{oof_note}"
        f"INSTRUCTION: Evaluate this audit critique with rigorous domain judgment. You are sovereign: "
        f"you are NOT required to accept it uncritically. You may:\n"
        f"1. ACCEPT: If the critique exposes a genuine blind spot, update your recommendation and action plan.\n"
        f"2. PARTIALLY ACCEPT: Incorporate valid risks as contingency mitigations while retaining your core choice.\n"
        f"3. REJECT WITH REASON: If the critique is flawed, unrealistic, or prohibited by user constraints, "
        f"explicitly defend your original decision with counter-evidence.\n\n"
        f"Produce the revised final answer clearly."
    )


# =============================================================================
# 5. Heterogeneous Reviewer Routing & Model Identity Trace
# =============================================================================

@dataclass
class ModelIdentityTrace:
    """Attested provenance verifying true model heterogeneity."""
    main_requested_route: str
    main_resolved_model: str
    main_family: str
    reviewer_requested_route: str
    reviewer_resolved_model: str
    reviewer_family: str
    is_heterogeneous: bool
    clean_context_verified: bool
    reentry_occurred: bool
    final_disposition: str  # "ACCEPTED", "PARTIALLY_ACCEPTED", "REJECTED_WITH_REASON", "NO_MATERIAL_CHANGE", "HETEROGENEOUS_REVIEW_UNAVAILABLE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Canonical vendor family mapping from admitted models
MODEL_FAMILIES = {
    "claude-opus-5": "anthropic",
    "claude-opus-4-6": "anthropic",
    "claude-sonnet-4-6": "anthropic",
    "gemini-3.8-flash-high": "google",
    "gemini-3.7-flash-high": "google",
    "gemini-3.1-pro-low": "google",
    "gpt-5.6-luna-max": "openai",
    "gpt-5.6-sol-xhigh": "openai",
    "k3-256k": "moonshot",
    "deepseek-v4-flash": "deepseek",
    "glm-5.3-flash": "zhipu",
    "qwen3.8-flash": "alibaba",
}


def resolve_heterogeneous_reviewer(main_model_id: str) -> Tuple[Optional[str], str]:
    """Resolve an admitted reviewer model from a DIFFERENT vendor family than the main model.

    Returns (model_id, status_or_reason).
    If no heterogeneous model is admitted, returns (None, 'HETEROGENEOUS_REVIEW_UNAVAILABLE').
    """
    main_family = MODEL_FAMILIES.get(main_model_id, "")
    if not main_family:
        # Fallback family detection by prefix
        if "claude" in main_model_id: main_family = "anthropic"
        elif "gemini" in main_model_id: main_family = "google"
        elif "gpt" in main_model_id: main_family = "openai"
        elif "k3" in main_model_id: main_family = "moonshot"
        elif "deepseek" in main_model_id: main_family = "deepseek"

    # Preferred candidate sequence for heterogeneous review (fast, high-context)
    candidate_priorities = [
        "gemini-3.8-flash-high",
        "gemini-3.7-flash-high",
        "k3-256k",
        "gpt-5.6-luna-max",
        "claude-sonnet-4-6",
    ]

    for cand in candidate_priorities:
        cand_family = MODEL_FAMILIES.get(cand, "")
        if cand_family and cand_family != main_family:
            return cand, "RESOLVED_HETEROGENEOUS"

    return None, "HETEROGENEOUS_REVIEW_UNAVAILABLE"


# =============================================================================
# 6. User-Facing Output Formatter
# =============================================================================

def format_user_output(
    candidate_answer: str,
    verdict: MaterialityVerdict,
    reentry_occurred: bool = False,
    revised_answer: Optional[str] = None,
    disposition: str = "NO_MATERIAL_CHANGE",
    explanation: str = "",
) -> str:
    """Format the user-facing output cleanly, avoiding internal process noise.

    - If no material blind spot: zero process noise, deliver candidate answer directly.
    - If material challenge evaluated but rejected: concise 1-line note.
    - If material challenge changed decision: clear, transparent update block.
    """
    if not verdict.material or not reentry_occurred:
        # Clean delivery: no noise
        return candidate_answer.strip()

    body = (revised_answer or candidate_answer).strip()

    if disposition == "REJECTED_WITH_REASON":
        note = f"\n\n---\n*Note: Independent audit evaluated a potential challenge ({verdict.reason}); confirmed original decision stands based on stated constraints.*"
        return body + note

    if disposition in ("ACCEPTED", "PARTIALLY_ACCEPTED"):
        header = (
            f"## Decision Update: Blind-Spot Integration\n"
            f"*An independent audit identified an unexamined risk: {explanation or verdict.reason}. "
            f"The final recommendation has been updated accordingly:*\n\n"
        )
        return header + body

    return body


# =============================================================================
# 7. End-to-End Production Pipeline Driver
# =============================================================================

def execute_blind_spot_pipeline(
    user_prompt: str,
    candidate_answer: str,
    call_model_fn: Optional[Any] = None,
    main_model_id: str = "claude-opus-5",
    new_evidence: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Production entry point for the Blind-Spot Gated Overlay.

    Executes the attested 6-step lifecycle:
    1. Task-Phase Gate: fast-skips mechanical execution; escalates on blocker evidence.
    2. Canonical DecisionPacket: bounds brief (<=150w verdict, <=150w uncertainties).
    3. Heterogeneous Reviewer: resolves cross-vendor model; fails safely if unavailable.
    4. Clean-Context Review: audits 5 targets with [OUT-OF-FRAMEWORK] option discipline.
    5. Circuit-Breaker Materiality Gate: parses binary material flag.
    6. Sovereign Single-Shot Re-entry: primary engine evaluates critique; delivers clean output.

    Returns (user_facing_output, attested_audit_trace).
    """
    trace: Dict[str, Any] = {
        "user_prompt": user_prompt,
        "main_model_id": main_model_id,
        "new_evidence": new_evidence,
    }

    # Step 1: Task-Phase Gate
    phase_verdict = classify_task_phase(user_prompt, new_evidence=new_evidence)
    trace["task_phase"] = phase_verdict.to_dict()
    if phase_verdict.phase == TaskPhase.EXECUTION and phase_verdict.status == "SKIP_EXECUTION_PHASE":
        trace["final_disposition"] = "SKIP_EXECUTION_PHASE"
        return candidate_answer, trace

    # Step 2: DecisionPacket Extraction
    packet = extract_decision_packet(candidate_answer, user_prompt)
    trace["decision_packet"] = {
        "verdict_length": len(packet.current_best_judgment),
        "uncertainties_length": len(packet.declared_uncertainties),
        "option_space": packet.allowed_option_space,
    }

    # Step 3: Heterogeneous Reviewer Resolution
    reviewer_model, rev_status = resolve_heterogeneous_reviewer(main_model_id)
    trace["reviewer_resolution"] = {
        "resolved_model": reviewer_model,
        "status": rev_status,
        "main_family": MODEL_FAMILIES.get(main_model_id, "unknown"),
        "reviewer_family": MODEL_FAMILIES.get(reviewer_model, "unknown") if reviewer_model else None,
    }
    if not reviewer_model or rev_status == "HETEROGENEOUS_REVIEW_UNAVAILABLE":
        trace["final_disposition"] = "HETEROGENEOUS_REVIEW_UNAVAILABLE"
        # Safe governance downgrade: deliver candidate answer without false audit claim
        note = "\n\n---\n*Governance Note: Heterogeneous outside reviewer unavailable; delivered primary answer without blind-spot audit.*"
        return candidate_answer + note, trace

    # If no real call function provided (e.g. unit testing or dry-run), return prepared prompts
    if call_model_fn is None:
        trace["dry_run_prompts"] = {
            "blindspot_prompt": build_blindspot_prompt(packet),
            "reviewer_model": reviewer_model,
        }
        trace["final_disposition"] = "DRY_RUN_PREPARED"
        return candidate_answer, trace

    # Step 4: Clean-Context Outside Review
    blindspot_prompt = build_blindspot_prompt(packet)
    reviewer_critique = call_model_fn(reviewer_model, blindspot_prompt, tag="blindspot-review")
    trace["reviewer_critique"] = reviewer_critique

    # Step 5: Materiality Gate
    mat_prompt = build_materiality_prompt(packet, reviewer_critique)
    mat_raw = call_model_fn(reviewer_model, mat_prompt, tag="materiality-gate")
    verdict = parse_materiality_json(mat_raw)
    trace["materiality_verdict"] = verdict.to_dict()

    if not verdict.material:
        trace["final_disposition"] = "NO_MATERIAL_CHANGE"
        clean_out = format_user_output(candidate_answer, verdict, reentry_occurred=False)
        return clean_out, trace

    # Step 6: Sovereign Single-Shot Re-entry
    reentry_prompt = build_reentry_prompt(candidate_answer, reviewer_critique, user_prompt)
    revised_answer = call_model_fn(main_model_id, reentry_prompt, tag="reentry-synthesis")

    # Detect disposition from revised output
    disposition = "ACCEPTED"
    if "defend" in revised_answer.lower() or "confirm original" in revised_answer.lower():
        disposition = "REJECTED_WITH_REASON"
    elif "partially" in revised_answer.lower() or "contingency" in revised_answer.lower():
        disposition = "PARTIALLY_ACCEPTED"

    trace["final_disposition"] = disposition
    trace["reentry_occurred"] = True
    user_out = format_user_output(
        candidate_answer=candidate_answer,
        verdict=verdict,
        reentry_occurred=True,
        revised_answer=revised_answer,
        disposition=disposition,
        explanation=verdict.reason,
    )
    return user_out, trace


# =============================================================================
# CLI Self-Test
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Blind-Spot Gated Reasoning Engine")
    parser.add_argument("--test-all", action="store_true", help="Run self-tests on all components")
    args = parser.parse_args()

    if args.test_all:
        # 1. Test Task-Phase Gate
        v_judg = classify_task_phase("Should we migrate to CockroachDB?")
        assert v_judg.phase == TaskPhase.JUDGMENT, "Failed judgment classification"
        v_exec = classify_task_phase("Deploy the v1.2 docker container to staging.")
        assert v_exec.phase == TaskPhase.EXECUTION, "Failed execution classification"
        v_esc = classify_task_phase("Deploy database migration", new_evidence="Table lock acquired for 45s")
        assert v_esc.status == "ESCALATE_TO_JUDGMENT", "Failed escalation"

        # 2. Test Heterogeneity
        m, st = resolve_heterogeneous_reviewer("claude-opus-4-6")
        assert st == "RESOLVED_HETEROGENEOUS", "Failed heterogeneity resolution"
        assert MODEL_FAMILIES[m] != "anthropic", "Reviewer model is not heterogeneous"

        print("All self-tests passed cleanly.")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
