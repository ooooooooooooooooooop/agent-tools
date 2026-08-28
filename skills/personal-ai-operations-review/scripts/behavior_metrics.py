#!/usr/bin/env python3
"""behavior_metrics.py — 从 DSH 会话提取的真实用户消息计算行为基线指标（只读）。

来源：PERSONAL_AI_PREFERENCE_CALIBRATION 产物（output/pref-calibration/behavior_metrics.py）
迁入本 skill 包作为复用引擎；逻辑不变，仅将计算封装为 compute() 供 personalization_status 调用。
输入：extract_user_msgs.py 产出的 user_messages.jsonl（{"session","text",...} 每行一条）。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

CORRECTION = re.compile(
    r"(答非所问|不是这个意思|不是我要的|不要再问|不要问|别问|直接做|太啰嗦|太长|你理解错了|我说过|我说过了|说了.+还|不要通用|越界|为什么又|怎么又|重新做|重新来|重做|能不能干|自己看一下|编造|胡说|瞎编|跑偏|你都没|没做到还|停下来干什么|不[要用]问我|不要轻易|不对吧|不对[，。]|错了)")
ACCEPT = re.compile(r"^(可以|不错|很好|对|就这样|通过|接受|同意|行|好的|完美|满意|符合预期|没问题)[。！!，,]?(|$)")
CONTINUE = re.compile(r"^(继续|接着|往下|keep going|go on)[，,。]?(.*)")


def load_messages(path: str | Path) -> list[dict]:
    """读取并去重（同 session 同文本前缀视为 resume 重放）。"""
    with open(path, encoding="utf-8") as fh:
        msgs = [json.loads(l) for l in fh]
    seen: set = set()
    uniq: list[dict] = []
    for m in msgs:
        k = (m.get("session"), (m.get("text") or "")[:200])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)
    return uniq


def compute(messages: list[dict]) -> dict:
    """返回行为基线指标。correction 为候选触发器，需结合上下文人工确认。"""
    n_sess = len({m.get("session") for m in messages}) or 1
    cnt: Counter = Counter()
    corrections: list[dict] = []
    for m in messages:
        t = " ".join((m.get("text") or "").split())
        if CORRECTION.search(t):
            cnt["correction"] += 1
            corrections.append(m)
        elif ACCEPT.search(t):
            cnt["accept"] += 1
        elif CONTINUE.search(t):
            cnt["continue"] += 1
    sess_with_corr = len({m.get("session") for m in corrections})
    multi_corr_sessions = sum(
        1 for c in Counter(m.get("session") for m in corrections).values() if c >= 2)
    return {
        "uniq_user_msgs": len(messages),
        "sessions": n_sess,
        "correction_events": cnt["correction"],
        "accept": cnt["accept"],
        "continue": cnt["continue"],
        "correction_rate": cnt["correction"] / max(len(messages), 1),
        "sessions_with_correction": sess_with_corr,
        "sessions_with_correction_rate": sess_with_corr / n_sess,
        "sessions_with_repeat_correction": multi_corr_sessions,
        "corrections": corrections,  # 原始消息列表，供分类器复用
    }


def main() -> None:
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("usage: behavior_metrics.py <user_messages.jsonl>")
        return
    r = compute(load_messages(path))
    print(f"uniq_user_msgs={r['uniq_user_msgs']}  sessions={r['sessions']}")
    print(f"correction_events={r['correction_events']}  accept={r['accept']}  continue={r['continue']}")
    print(f"correction_rate_per_msg={r['correction_rate']:.1%}")
    print(f"sessions_with_correction={r['sessions_with_correction']}/{r['sessions']}"
          f" ({r['sessions_with_correction_rate']:.0%})")
    print(f"sessions_with_repeat_correction={r['sessions_with_repeat_correction']}")


if __name__ == "__main__":
    main()
