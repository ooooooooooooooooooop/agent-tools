#!/usr/bin/env python3
"""find_session.py — 按对话标题关键词快速定位 DSH 会话并读取内容/统计。

三条路径合一，替代"遍历目录 + 逐个解压"的原始方式：
  python scripts/find_session.py <关键词>            # 按标题搜索，列出匹配会话
  python scripts/find_session.py --id <session-id>   # 读取该会话的消息内容
  python scripts/find_session.py --id <id> --stats   # token/时间消耗统计

数据源：
  - 标题索引 ~/.dsh/storages/session_projcache.json（title/cwd/createdAt/sessionStats）
  - 内容 ~/.dsh/sessions/<项目>/<session>/session.jsonl.zstd（多帧 zstd 事件流）
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

DSH_HOME = Path.home() / ".dsh"
PROJCACHE = DSH_HOME / "storages" / "session_projcache.json"
SESSIONS_DIR = DSH_HOME / "sessions"


def _out(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def load_projcache() -> dict:
    with open(PROJCACHE, encoding="utf-8") as f:
        return json.load(f)["tables"]["sessions"]


def fmt_ts(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def search(keyword: str) -> list[dict]:
    """按标题关键词搜索，返回匹配会话列表。"""
    sessions = load_projcache()
    kw = keyword.lower()
    hits = []
    for sid, s in sessions.items():
        rows = s.get("rows", {})
        title = (rows.get("title") or {}).get("val") or ""
        if kw in title.lower():
            hits.append(
                {
                    "id": sid,
                    "title": title,
                    "cwd": s.get("identity", {}).get("cwd", ""),
                    "created": s.get("identity", {}).get("createdAt", 0),
                    "stats": (rows.get("sessionStats") or {}).get("val") or {},
                }
            )
    hits.sort(key=lambda h: h["created"], reverse=True)
    return hits


def find_session_file(session_id: str) -> Path | None:
    """按 session-id 在全部项目目录下定位会话文件（目录命名有新旧两套，直接 glob 最稳）。"""
    short = session_id.replace("session-", "")
    for path in SESSIONS_DIR.glob(f"*/*{short}*/session.jsonl.zstd"):
        return path
    return None


def read_events(path: Path) -> list[dict]:
    """多帧 zstd 必须用 stream_reader；单帧 decompress 只解第一帧。"""
    import zstandard

    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as f, dctx.stream_reader(f) as reader:
        text = reader.read().decode("utf-8", errors="replace")
    events = []
    for line in text.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def show_content(session_id: str, limit: int = 0) -> None:
    path = find_session_file(session_id)
    if not path:
        _out(f"会话文件未找到: {session_id}")
        return
    events = read_events(path)
    n = 0
    for ev in events:
        t = ev.get("type")
        if t == "user/message":
            text = _text_of(ev.get("data", {}).get("content"))
            if not text:
                continue
            _out(f"\n[{fmt_ts(ev.get('time', 0))}] USER: {text[:300]}")
            n += 1
        elif t == "assistant/message":
            msg = ev.get("data", {}).get("message", {})
            text = _text_of(msg.get("content"))
            if not text:
                continue
            _out(f"[{fmt_ts(ev.get('time', 0))}] ASSISTANT: {text[:300]}")
            n += 1
        if limit and n >= limit:
            _out(f"\n... (已截断，共 {n} 条；--limit 0 看全部)")
            return
    _out(f"\n共 {n} 条消息")


def show_stats(session_id: str) -> None:
    path = find_session_file(session_id)
    if not path:
        _out(f"会话文件未找到: {session_id}")
        return
    events = read_events(path)
    # 事件计数
    from collections import Counter

    types = Counter(ev.get("type") for ev in events)
    times = [ev.get("time", 0) for ev in events if ev.get("time")]
    duration_min = (max(times) - min(times)) / 60000 if times else 0
    # projcache 聚合统计（token 级数据不在事件流里，在 sessionStats）
    sessions = load_projcache()
    short = session_id.replace("session-", "")
    stats = {}
    for sid, s in sessions.items():
        if short in sid:
            stats = (s.get("rows", {}).get("sessionStats") or {}).get("val") or {}
            break
    _out(f"会话: {session_id}")
    _out(f"时长: {duration_min:.0f} 分钟 | 回合: {types.get('turn/start', 0)} | 步骤: {types.get('step/start', 0)}")
    _out(f"工具调用: {types.get('tool/call', 0)} | 助手消息: {types.get('assistant/message', 0)} | 压缩: {types.get('compaction/start', 0)} 次")
    if stats:
        _out(
            f"LLM 耗时: {stats.get('llmMs', 0) / 1000:.0f}s | 首 token: {stats.get('ttftMs', 0) / 1000:.0f}s | 输出 token: {stats.get('decodeTokens', 0)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("keyword", nargs="?", help="标题关键词")
    parser.add_argument("--id", dest="session_id", help="直接按 session-id 操作")
    parser.add_argument("--content", action="store_true", help="搜索后直接读内容")
    parser.add_argument("--stats", action="store_true", help="输出 token/时间统计")
    parser.add_argument("--limit", type=int, default=0, help="内容条数上限（0=全部）")
    args = parser.parse_args()

    if args.session_id:
        if args.stats:
            show_stats(args.session_id)
        else:
            show_content(args.session_id, args.limit)
        return 0

    if not args.keyword:
        parser.print_help()
        return 1

    hits = search(args.keyword)
    if not hits:
        _out(f"无标题匹配: {args.keyword}")
        return 1
    _out(f"匹配 {len(hits)} 个会话（按时间倒序）:\n")
    for h in hits[:20]:
        st = h["stats"]
        _out(
            f"{fmt_ts(h['created'])} | {h['id']}\n"
            f"  项目: {h['cwd']} | 标题: {h['title']}\n"
            f"  回合 {st.get('turns', '?')} | 步骤 {st.get('steps', '?')} | LLM {st.get('llmMs', 0) / 1000:.0f}s | 输出 {st.get('decodeTokens', '?')} tok"
        )
    if args.content and hits:
        _out("\n" + "=" * 60 + "\n最新匹配的内容:\n")
        show_content(hits[0]["id"], args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
