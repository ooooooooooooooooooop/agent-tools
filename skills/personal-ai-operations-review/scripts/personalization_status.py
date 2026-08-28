#!/usr/bin/env python3
"""personalization_status.py — Personalization 域只读状态（personal-ai-operations-review 子组件）。

只读聚合： observe / aggregate / classify / explain / recommend。
禁止：修改 canonical / memory / preference / push / 修 drift / 关闭 blocker。

输入：
  --messages PATH        user_messages.jsonl（由 output/pref-calibration/extract_user_msgs.py 产出）
  --selection-events PATH  注入选择事件 JSONL：{"task_scope","injected_scopes","preference_count"}
                         （当前无真实 hook 数据源；缺省时相关指标标 UNKNOWN，不伪造精度）
  --baseline-report PATH 校准报告（Correction Rate 等基线的 SSOT，重新读取，不硬编码）
输出：一行 `Personalization: <STATUS> — <一句原因>`；--detail 打印指标明细。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import behavior_metrics  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
DEFAULT_MESSAGES = REPO / "output" / "pref-calibration" / "user_messages.jsonl"
DEFAULT_BASELINE = REPO / "output" / "pref-calibration" / "PERSONAL_AI_PREFERENCE_CALIBRATION_REPORT.md"

# 偏好相关纠正模式（命中即 personalization failure 候选）
PATTERN_GROUPS = {
    "token_saving": ["省token", "浪费token", "省 token", "太浪费"],
    "supervision_polling": ["轮询", "一直看", "巡检"],
    "delegate_cheap": ["执行端", "委派", "便宜", "贵token", "贵模型"],
    "no_fabrication": ["编造", "瞎编", "胡说"],
    "self_serve_no_ask": ["不要问", "这要问", "能不能干", "自己看", "不用问我", "一直问", "让我执行", "问我"],
    "no_human_in_loop": ["人工部分", "人工环节", "记录阻塞", "停下来干什么"],
    "search_channel": ["web search", "web_search", "官方搜索"],
    "verbosity": ["啰嗦", "太长", "冗余", "这么详细"],
    "scope_violation": ["越界", "没做到还改", "没让你"],
}
NEW_MODEL_ERROR = re.compile(r"(400|402|401|403|500|503|报错|失败|EOF|timeout|超时|接口|额度|Insufficient)")
KNOWLEDGE_ERROR = re.compile(r"(错了|不对|搞错|弄错|理解错)")
TASK_AMBIGUITY = re.compile(r"(哪个|什么意思|歧义|不清楚你指|是指)")


def classify_correction(text: str, repeat_groups: set[str]) -> str:
    """把一条用户纠正分类；命中偏好模式且该模式已跨会话重复 → REPEAT。"""
    t = " ".join(text.split())
    for group, kws in PATTERN_GROUPS.items():
        if any(kw in t for kw in kws):
            return ("REPEAT_PERSONALIZATION_FAILURE" if group in repeat_groups
                    else "PERSONALIZATION_FAILURE")
    if TASK_AMBIGUITY.search(t):
        return "TASK_AMBIGUITY"
    if NEW_MODEL_ERROR.search(t):
        return "NEW_MODEL_ERROR"
    if KNOWLEDGE_ERROR.search(t):
        return "KNOWLEDGE_ERROR"
    return "KNOWLEDGE_ERROR"  # 无法细分时的保守归类（候选，需人工确认）


def correction_groups(corrections: list[dict]) -> dict[str, set]:
    """每条纠正命中的偏好模式 → 出现会话集合（用于判定跨会话重复）。"""
    groups: dict[str, set] = {}
    for m in corrections:
        t = " ".join((m.get("text") or "").split())
        for group, kws in PATTERN_GROUPS.items():
            if any(kw in t for kw in kws):
                groups.setdefault(group, set()).add(m.get("session"))
    return groups


def load_selection_events(path: str | None) -> list[dict] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh]


def check_selection(events: list[dict] | None) -> dict:
    """Preference Selection 检查（只检查不修改）。无数据源 → UNKNOWN。"""
    if events is None:
        return {"status": "UNKNOWN", "scope_leakage": [], "over_personalization": [],
                "note": "无注入事件数据源（需要 Context Builder/hook 侧未来暴露选择事件）"}
    leakage, over = [], []
    for e in events:
        scope = e.get("task_scope") or ""
        injected = e.get("injected_scopes") or []
        if scope.startswith("project:"):
            foreign = [s for s in injected
                       if isinstance(s, str) and s.startswith("project:") and s != scope]
            if foreign:
                leakage.append({"task_scope": scope, "foreign": foreign})
        if scope in ("simple", "normal") and (e.get("preference_count") or 0) > 3:
            over.append({"task_scope": scope, "preference_count": e.get("preference_count")})
    status = "HEALTHY"
    if over:
        status = "DEGRADED"
    if leakage:
        status = "ACTION_REQUIRED"  # scope 泄漏是真实边界故障
    return {"status": status, "scope_leakage": leakage, "over_personalization": over}


def read_baseline(report_path: str | None) -> dict:
    """从校准报告重读基线数字（SSOT），失败则 UNKNOWN，不硬编码。"""
    p = Path(report_path) if report_path else DEFAULT_BASELINE
    if not p.is_file():
        return {"correction_rate": None, "repeat_sessions": None, "source": "missing"}
    text = p.read_text(encoding="utf-8")
    cr = re.search(r"Correction Rate[^\d]*(\d+(?:\.\d+)?)%", text)
    rs = re.search(r"sessions_with_repeat_correction|重复纠正", text)
    rep = re.search(r"(\d+)/\d+\s*会话.*?重复|(\d+)/\d+ sessions?.*?repeat", text)
    return {
        "correction_rate": float(cr.group(1)) / 100 if cr else None,
        "repeat_sessions": int(rep.group(1) or rep.group(2)) if rep else None,
        "source": str(p),
        "repeat_mentioned": bool(rs),
    }


def evaluate(messages_path: str | None, selection_events_path: str | None,
             baseline_report: str | None) -> dict:
    sel = check_selection(load_selection_events(selection_events_path))
    baseline = read_baseline(baseline_report)
    mp = Path(messages_path) if messages_path else DEFAULT_MESSAGES
    if not mp.is_file():
        return {"status": "UNKNOWN",
                "reason": f"缺少会话消息数据（先运行 extract_user_msgs.py 生成 {mp.name}）",
                "selection": sel, "baseline": baseline}
    r = behavior_metrics.compute(behavior_metrics.load_messages(mp))
    groups = correction_groups(r["corrections"])
    repeat_groups = {g for g, sess in groups.items() if len(sess) >= 2}
    classified = [classify_correction(m.get("text") or "", repeat_groups)
                  for m in r["corrections"]]
    n_repeat = sum(1 for c in classified if c == "REPEAT_PERSONALIZATION_FAILURE")
    n_pers = sum(1 for c in classified if c == "PERSONALIZATION_FAILURE")
    n_clar = len(groups.get("self_serve_no_ask", set()) and
                 [m for m in r["corrections"]
                  if any(kw in m.get("text", "") for kw in PATTERN_GROUPS["self_serve_no_ask"])])

    metrics = {
        "correction_rate": r["correction_rate"],
        "repeat_personalization_failures": n_repeat,
        "personality_failure_share": (n_repeat + n_pers) / max(r["correction_events"], 1),
        "unnecessary_clarification_events": n_clar,
        "repeat_sessions": r["sessions_with_repeat_correction"],
        "over_personalization": ("UNKNOWN" if sel["status"] == "UNKNOWN"
                                 else len(sel["over_personalization"])),
        "scope_leakage": ("UNKNOWN" if sel["status"] == "UNKNOWN"
                          else len(sel["scope_leakage"])),
    }

    # 状态判定：scope leakage 最优先；其次趋势恶化；再次 over-personalization。
    if sel["scope_leakage"]:
        status, reason = "ACTION REQUIRED", \
            f"检测到 memory scope 泄漏 {len(sel['scope_leakage'])} 起（project 跨注入）"
    else:
        worsened = []
        if baseline["correction_rate"] is not None and \
                r["correction_rate"] > baseline["correction_rate"] * 1.5:
            worsened.append(f"Correction Rate {r['correction_rate']:.1%} > 基线 "
                            f"{baseline['correction_rate']:.1%}×1.5")
        if baseline["repeat_sessions"] is not None and \
                r["sessions_with_repeat_correction"] > baseline["repeat_sessions"]:
            worsened.append(f"重复纠正会话 {r['sessions_with_repeat_correction']} > 基线 "
                            f"{baseline['repeat_sessions']}")
        if worsened:
            status, reason = "DEGRADED", "；".join(worsened)
        elif sel["over_personalization"]:
            status, reason = "DEGRADED", \
                f"简单/普通任务被注入超额 preference {len(sel['over_personalization'])} 起"
        else:
            approx = [] if sel["status"] != "UNKNOWN" else ["Over-Personalization/Scope Leakage=UNKNOWN(无注入事件源)"]
            base_txt = (f"{baseline['correction_rate']:.1%}"
                        if baseline["correction_rate"] is not None else "UNKNOWN")
            status = "HEALTHY"
            reason = (f"Correction Rate {r['correction_rate']:.1%}（基线 {base_txt}），"
                      f"重复纠正组 {len(repeat_groups)}；"
                      + ("；".join(approx) if approx else "无恶化趋势"))
    return {"status": status, "reason": reason, "metrics": metrics,
            "selection": sel, "baseline": baseline,
            "classified": dict(Counter(classified))}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--messages")
    ap.add_argument("--selection-events")
    ap.add_argument("--baseline-report")
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    r = evaluate(a.messages, a.selection_events, a.baseline_report)
    print(f"Personalization: {r['status']} — {r['reason']}")
    if a.detail:
        print(json.dumps({k: v for k, v in r.items() if k != "selection" or True},
                         ensure_ascii=False, indent=1, default=str))
    return {"HEALTHY": 0, "UNKNOWN": 0}.get(r["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
