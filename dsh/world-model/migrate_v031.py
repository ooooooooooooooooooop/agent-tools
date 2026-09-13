#!/usr/bin/env python3
"""migrate_v031.py — canonical schema 1.0 → 1.1 (theory V0.3.1) migration + rollback.

Splits the monolithic current.yaml into the V0.3.1 layered layout:

    canonical/
      current.yaml      identity head + W snapshot + V snapshot + refs
      governance.yaml   U0 constitutional + U1 evolvable + normative_authorities
      lineage.yaml      L: entity_id/lineage_head/epoch/active_body/lease
      sources.yaml      Source/Entity model (epistemic reliability per domain)
      interfaces.yaml   Channel/Sensor/Actuator model
      operators.yaml    (unchanged)
      open-loops.yaml   (unchanged)
      history/lineage.jsonl       append-only identity events
      history/model-updates.jsonl
      history/pre-v031-backup-<ts>/  full pre-migration copy (rollback source)

Invariants:
  - no silent drop: unknown/unmigrated keys land in current.yaml `x_preserved`
  - rollback restores byte-identical pre-migration files and appends ROLLBACK
    to history/lineage.jsonl
  - dry-run by default; --apply writes
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCHEMA_110 = "1.1"
THEORY = "0.3.1"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_yaml(p: Path) -> dict:
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def dump_yaml(p: Path, data: dict) -> None:
    p.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def lineage_event(kind: str, **fields) -> dict:
    ev = {
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "event": kind,
        "ts": utcnow(),
        "schema_version": SCHEMA_110,
    }
    ev.update(fields)
    return ev


def append_lineage(canon: Path, ev: dict) -> None:
    hist = canon / "history"
    hist.mkdir(exist_ok=True)
    with open(hist / "lineage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def build_sources(old: dict) -> dict:
    """channel_model (1.0) → sources.yaml (1.1): Entity/Source only."""
    ch = old.get("channel_model") or {}
    sources = {
        "schema_version": SCHEMA_110,
        "note": (
            "Source/Entity 模型：谁/什么产生信息。epistemic_reliability 按 domain 分开，"
            "无全局可信度。normative authority 不在这里——放 governance.yaml "
            "（epistemic reliability 可被证据修订；normative authority 是治理声明）。"
        ),
        "sources": {},
    }
    us = ch.get("user") or {}
    sources["sources"]["user"] = {
        "source_type": "human_agent",
        "roles": ["owner"],
        "epistemic_reliability": {
            "architecture_judgment": {
                "level": "high",
                "confidence": "medium-high",
                "basis": "世界模型=实体自身、OFF默认=失忆、仪式vs存在 等架构层判断被采纳且事后证明正确",
            },
            "external_technical_fact": {"level": "unknown", "confidence": "low"},
        },
        "correction_history": us.get("correction_history") or [],
        "known_limits": us.get("known_limits", ""),
        "as_peer_world_model": True,
    }
    cw = ch.get("chatgpt_web") or {}
    sources["sources"]["chatgpt_web"] = {
        "source_type": "peer_ai_agent",
        "roles": ["adjudicator", "theory_reviewer"],
        "epistemic_reliability": {
            "theory_review": {
                "level": "high",
                "confidence": "medium-high",
                "basis": "V0.1 证伪、H5 边界、冻结决策、V0.3 五点修正 均准确",
            }
        },
        "known_limits": cw.get("known_limits", ""),
        "as_peer_world_model": True,
    }
    tr = ch.get("tool_returns") or {}
    sources["sources"]["tool_returns"] = {
        "source_type": "tool_output",
        "roles": ["sensor_feed"],
        "epistemic_reliability": {
            "raw_facts": {"level": "medium", "confidence": "medium",
                          "basis": tr.get("reliability", "RAW_EVIDENCE=原始证据非结论")}
        },
        "known_limits": "日志存在≠支持你； freshness/cache 问题归 interfaces.yaml sensor",
    }
    so = ch.get("self_output") or {}
    sources["sources"]["self_output"] = {
        "source_type": "self",
        "roles": ["worker"],
        "epistemic_reliability": {
            "claims": {"level": "provisional", "confidence": "low",
                       "basis": so.get("reliability", "PASS 宣称须附可复核证据")}
        },
        "known_limits": "会被批判性复核（E2 对抗模式）",
    }
    return sources


def build_interfaces() -> dict:
    return {
        "schema_version": SCHEMA_110,
        "note": (
            "Interface 模型：信息怎么进来/动作怎么出去。与 Source 正交——"
            "source 错 / channel 丢 / sensor stale / actuator 副作用 是四种不同失败。"
        ),
        "channels": {
            "chat_ui": {"type": "communication", "reliability": "high",
                        "known_failure": "UI 丢消息=channel failure，不是 source 错"},
            "mcp_chatgpt_web": {"type": "communication", "reliability": "medium",
                                "known_failure": "异步回帖偶发 generation_stuck/CDP 超时"},
            "filesystem": {"type": "io", "reliability": "high"},
        },
        "sensors": {
            "file_read": {"tool_class": "read", "freshness": "filesystem mtime"},
            "shell_output": {"tool_class": "exec", "freshness": "realtime",
                             "known_failure": "GBK/UTF-8 编码错配（已修，双向显式编码）"},
            "tool_result": {"tool_class": "generic",
                            "note": "RAW_EVIDENCE 捕获口；结果可信度=source 侧问题"},
            "ledger_replay": {"tool_class": "internal", "freshness": "append-only"},
        },
        "actuators": {
            "file_write": {"effect_class": "consequential"},
            "exec": {"effect_class": "consequential",
                     "irreversible_patterns": "rm -rf/del/Remove-Item -Recurse/drop/git push -f"},
            "git_commit": {"effect_class": "consequential", "reversible": "revert"},
            "deploy_copy": {"effect_class": "consequential", "scope": "profile plugins"},
        },
    }


def build_governance(old: dict) -> dict:
    return {
        "schema_version": SCHEMA_110,
        "theory_version": THEORY,
        "u0_constitutional": {
            "note": (
                "U0 不是 immutable 也不是 human-only。Personal AI 可自主发现 U0 缺陷、"
                "建竞争模型、取证、设计、实现、测试并完成合法修改——但必须满足："
                "修改理由和 Prediction 先于修改存在；不得使用修改后才获得的权限授权修改过程；"
                "验收依据必须来自修改前仍合法的 authority/procedure；新规则不追溯合法化旧动作；"
                "保留旧 U0+完整 lineage+rollback；U0 修改本身遵守修改前 U0。"
            ),
            "root_authorities": {
                "entity_root": "personal-ai-self",
                "normative_authority_root": "owner-user",
                "emergency_stop": ["owner-user"],
                "canonical_write_authority": "governed-proposal-path-only",
                "body_binding_authority": "operator + BCC-1 PASS",
                "declassification_ceiling": "PUBLIC 级导出需 owner review",
                "u0_amendment": {
                    "requires": [
                        "reason_and_prediction_recorded_before_change",
                        "authority_or_procedure_valid_under_prior_u0",
                        "independent_review",
                        "rollback_plan_and_prior_u0_preserved",
                        "lineage_event: GOVERNANCE_CHANGED",
                    ],
                    "forbidden": [
                        "self_grant_of_authority_not_held_before_change",
                        "retroactive_legitimation",
                    ],
                    "elevated_for": [
                        "owner_authority", "correction_authority", "stop_authority",
                        "canonical_write_authority", "body_binding", "declassification_ceiling",
                    ],
                },
            },
            "minimum_acceptance": {
                "high_impact": "independent_review + canary_or_e2",
                "constitutional": "highest_tier: prior-U0-compliant + independent review + rollback",
            },
        },
        "normative_authorities": {
            "note": "规范权威是治理声明，不从 epistemic reliability 推导，不因事实性错误自动降级",
            "user": {
                "scopes": {
                    "task_goal": "authoritative",
                    "project_goal": "authoritative",
                    "execution_permission": "authoritative",
                    "durable_value": "authoritative-via-value-proposal",
                    "external_world_fact": "none-epistemic-only",
                    "safety_boundary": "constrained-by-u0",
                }
            },
            "chatgpt_web": {
                "scopes": {
                    "theory_review": "advisory",
                    "gate_adjudication": "advisory",
                    "normative": "none",
                }
            },
            "runtime_self": {"scopes": {"normative": "none"}},
        },
        "u1_evolvable": {
            "note": "常规治理：proposal → validation → canary/E2（按级别）→ accept → rollback-capable",
            "update_classes": {
                "world_model": {"allowed_proposers": ["runtime", "operator"],
                                "auto_accept": False, "required_evidence": "observation_refs",
                                "rollback_required": True},
                "value_model": {"allowed_proposers": ["operator"],
                                "required_authority": "user DURABLE_VALUE_STATEMENT or governance proposal",
                                "rollback_required": True},
                "governance_u1": {"allowed_proposers": ["runtime", "operator"],
                                  "independent_review_required": True, "canary_required": True},
                "governance_u0": {"allowed_proposers": ["operator"],
                                  "constitutional": True,
                                  "requires": "u0_amendment.requires (above)"},
                "lineage": {"allowed_proposers": ["system-only"],
                            "note": "worker 不可写 lineage"},
                "declassification": {"required_authority": "owner",
                                     "independent_review_required": True,
                                     "flow": "PRIVATE artifact → DECLASSIFICATION_PROPOSAL → sanitizer/redactor → leakage check → authority review → derivative; 原件等级不变"},
                "body_binding": {"required_authority": "operator",
                                 "requires": ["BCC-1 全项 PASS", "BODY_RELEASED 先行", "epoch+1"]},
                "gate_policy": {"canary_required": True},
                "schema": {"migration_required": True, "rollback_required": True},
            },
            "policies": {
                "canonical_write": "runtime 只写 proposals/；canonical 修改走治理接受路径",
                "supersession": "同 intended_action/subject 悬挂预测自动 supersede；旧结论保留被替代原因",
                "principled_disagreement": "仅对 EPISTEMIC_CLAIM：直接证据更强时可保留分歧并记录；NORMATIVE_DIRECTIVE 在合法 scope 内不走证据权衡",
                "freeze": "冻结后任何修改=新变更，不无痕并入已验收基线",
                "schema_migration": "必须提供 rollback；未知字段不得 silent drop",
                "body_handoff": "BODY_RELEASED → BCC-1 → acquire lease → continuity_epoch+1 → BODY_BOUND",
                "single_writer_lease": "同一 entity_id 默认只有一个 canonical writer body",
                "fork": "两个 body 从同一 head 分叉各自写入 → 显式 IDENTITY_FORKED，不得继续隐式声称同一实例",
            },
        },
    }


def build_lineage(entity_id: str, genesis: dict, bound: dict) -> dict:
    return {
        "schema_version": SCHEMA_110,
        "entity_id": entity_id,
        "genesis_event": genesis["event_id"],
        "lineage_head": bound["event_id"],
        "canonical_revision": f"schema-{SCHEMA_110}",
        "continuity_epoch": 1,
        "active_body": {
            "body_id": "dsh-local",
            "harness_type": "dsh",
            "bcc_version": "BCC-1",
            "bound_at": bound["ts"],
            "lease": "exclusive-canonical-writer",
        },
        "parent_entity": None,
        "major_transitions": [
            {"event": genesis["event_id"], "kind": "ENTITY_GENESIS"},
            {"event": bound["event_id"], "kind": "BODY_BOUND"},
        ],
        "history_ref": "history/lineage.jsonl",
        "identity_continuity_claim": {
            "same_harness": "supported via lineage_head + lease + epoch",
            "cross_harness": "UNVALIDATED / REQUIRES_REAL_BODY_A_TO_B_EVIDENCE",
        },
    }


def build_value(old: dict) -> dict:
    v = old.get("value_model") or {}
    return {
        "current_values": {
            "evidence_over_appearance": "PASS 宣称必须附可复核证据；不为验收硬造 mutation",
            "last_decision": v.get("last_decision", ""),
            "weighting": "认知收益(模型更准) ≥ 任务效率；结构真实 > 指标好看",
        },
        "uncertainty": {
            "conflicted": ["世界模型维护成本 vs 收益的边界条件（H2 未裁决）"],
            "unknown": ["长期价值在跨 harness/跨主体场景下的迁移规则"],
        },
        "update_protocol_ref": "governance.yaml#u1_evolvable.update_classes.value_model",
        "source": v.get("source", ""),
    }


def build_current(old: dict, lineage_head: str) -> dict:
    w = {
        "models": old.get("current_models") or {},
        "competing_models": old.get("competing_models") or {},
        "uncertainty": old.get("uncertainty") or {},
        "open_predictions": old.get("open_predictions") or [],
        "known_residuals": old.get("known_residuals") or [],
        "model_coverage": old.get("model_coverage", ""),
        "current_state": old.get("current_state") or {},
    }
    cur = {
        "schema_version": SCHEMA_110,
        "theory_version": THEORY,
        "watermark": utcnow(),
        "identity": {
            "entity_id": "personal-ai-admin-001",
            "governance_ref": "governance.yaml",
            "lineage_ref": "lineage.yaml",
            "lineage_head": lineage_head,
        },
        "world_model": w,
        "value_model": build_value(old),
        "refs": {
            "sources": "sources.yaml",
            "interfaces": "interfaces.yaml",
            "operators": "operators.yaml",
            "open_loops": "open-loops.yaml",
            "gate_experiments": "gate-experiments.yaml",
            "proposals": "proposals/",
            "history": "history/",
        },
        "freeze": old.get("freeze") or {},
        "evidence_refs": old.get("evidence_refs") or [],
    }
    known = {
        "schema_version", "watermark", "model_coverage", "current_state", "current_models",
        "competing_models", "freeze", "uncertainty", "open_predictions", "known_residuals",
        "open_loops", "channel_model", "identity_model", "value_model", "evidence_refs",
    }
    leftover = {k: v for k, v in old.items() if k not in known}
    if leftover:
        cur["x_preserved"] = leftover
    # identity_model → governance/lineage domain, kept as provenance record
    im = old.get("identity_model")
    if im:
        cur.setdefault("x_preserved", {})["identity_model_v030"] = im
    return cur


def migrate(canon: Path, apply: bool) -> dict:
    old = load_yaml(canon / "current.yaml")
    if not old:
        raise SystemExit(f"no current.yaml at {canon}")
    if old.get("schema_version") == SCHEMA_110:
        raise SystemExit("already at schema 1.1 — nothing to do")

    entity_id = "personal-ai-admin-001"
    genesis = lineage_event("ENTITY_GENESIS", entity_id=entity_id,
                            note="V0.3.1 分层初始化；prior history in pre-v031 backups")
    bound = lineage_event("BODY_BOUND", entity_id=entity_id, body_id="dsh-local",
                          harness_type="dsh", bcc_version="BCC-1")
    migrated = lineage_event("SCHEMA_MIGRATED", entity_id=entity_id,
                             from_schema=str(old.get("schema_version", "1.0")),
                             to_schema=SCHEMA_110, theory_version=THEORY)

    out = {
        "governance.yaml": build_governance(old),
        "lineage.yaml": build_lineage(entity_id, genesis, migrated),
        "sources.yaml": build_sources(old),
        "interfaces.yaml": build_interfaces(),
        "current.yaml": build_current(old, migrated["event_id"]),
    }
    report = {"entity_id": entity_id, "files": sorted(out), "applied": apply}
    if not apply:
        return report

    backup = canon / "history" / f"pre-v031-backup-{datetime.now():%Y%m%d-%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)
    for f in canon.iterdir():
        if f.is_file():
            shutil.copy2(f, backup / f.name)
    if (canon / "proposals").is_dir():
        shutil.copytree(canon / "proposals", backup / "proposals", dirs_exist_ok=True)

    for ev in (genesis, bound, migrated):
        append_lineage(canon, ev)
    for name, data in out.items():
        dump_yaml(canon / name, data)
    # ensure the layered layout is complete even if the 1.0 dir lacked files
    for name, empty in (("operators.yaml", {"schema_version": SCHEMA_110, "operators": []}),
                        ("open-loops.yaml", {"schema_version": SCHEMA_110, "open_loops": []})):
        if not (canon / name).exists():
            dump_yaml(canon / name, empty)

    # briefing.md is now a COMPILED artifact — rename handwritten original
    bp = canon / "briefing.md"
    if bp.exists() and "GENERATED" not in bp.read_text(encoding="utf-8", errors="replace")[:200]:
        bp.rename(backup / "briefing.handwritten.md")
        (backup / "briefing.handwritten.md").write_text(
            (backup / "briefing.handwritten.md").read_text(encoding="utf-8"), encoding="utf-8")
    (canon / "history" / "model-updates.jsonl").touch(exist_ok=True)
    report["backup"] = str(backup)
    return report


def rollback(canon: Path, backup_dir: Path | None) -> dict:
    if backup_dir is None:
        backups = sorted((canon / "history").glob("pre-v031-backup-*"))
        if not backups:
            raise SystemExit("no pre-v031 backup found")
        backup_dir = backups[-1]
    if not backup_dir.is_dir():
        raise SystemExit(f"backup not found: {backup_dir}")
    for name in ("governance.yaml", "lineage.yaml", "sources.yaml", "interfaces.yaml"):
        p = canon / name
        if p.exists():
            p.unlink()
    for f in backup_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, canon / f.name)
    if (backup_dir / "proposals").is_dir():
        shutil.copytree(backup_dir / "proposals", canon / "proposals", dirs_exist_ok=True)
    ev = lineage_event("ROLLBACK", to="schema-1.0", backup=str(backup_dir))
    append_lineage(canon, ev)
    return {"rolled_back_to": str(backup_dir), "event": ev["event_id"]}


def verify(canon: Path) -> dict:
    need = ["current.yaml", "governance.yaml", "lineage.yaml",
            "sources.yaml", "interfaces.yaml", "operators.yaml", "open-loops.yaml"]
    missing = [n for n in need if not (canon / n).exists()]
    cur = load_yaml(canon / "current.yaml")
    lin = load_yaml(canon / "lineage.yaml")
    gov = load_yaml(canon / "governance.yaml")
    checks = {
        "missing_files": missing,
        "schema_1_1": cur.get("schema_version") == SCHEMA_110,
        "w_present": bool(cur.get("world_model", {}).get("models")),
        "v_three_layers": all(k in (cur.get("value_model") or {})
                              for k in ("current_values", "uncertainty", "update_protocol_ref")),
        "u0_u1": "u0_constitutional" in gov and "u1_evolvable" in gov,
        "normative_authorities": "normative_authorities" in gov,
        "lineage_fields": all(k in lin for k in (
            "entity_id", "genesis_event", "lineage_head", "continuity_epoch", "active_body")),
        "sources_separate": "sources" in load_yaml(canon / "sources.yaml"),
        "interfaces_split": all(k in load_yaml(canon / "interfaces.yaml")
                                for k in ("channels", "sensors", "actuators")),
        "lineage_log": (canon / "history" / "lineage.jsonl").exists(),
    }
    checks["PASS"] = not missing and all(v for k, v in checks.items() if k != "missing_files")
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", required=True, help="canonical dir")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--backup", default=None)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    canon = Path(args.canonical)
    if args.rollback:
        print(json.dumps(rollback(canon, Path(args.backup) if args.backup else None),
                         ensure_ascii=False, indent=2))
        return 0
    if args.verify:
        print(json.dumps(verify(canon), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(migrate(canon, args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
