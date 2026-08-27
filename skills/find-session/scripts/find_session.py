#!/usr/bin/env python3
"""find_session.py — 按对话标题/内容关键词快速定位 DSH 会话并读取内容/统计。

一条命令替代"遍历目录 + 逐个解压"的原始方式：

  # 标题搜索（快，走 projcache 索引）
  python scripts/find_session.py <关键词>                # 按标题搜索，列出匹配会话
  python scripts/find_session.py <关键词> --content      # 搜索后直接读最新匹配会话内容
  python scripts/find_session.py <关键词> --project 桌面  # 只搜指定项目路径下的会话

  # 正文全文搜索（慢，需解压 zstd；默认只扫最近 30 个会话）
  python scripts/find_session.py --grep <关键词>          # 在会话正文里搜关键词
  python scripts/find_session.py --grep <关键词> --full   # 全量扫描所有会话

  # 按 id 操作
  python scripts/find_session.py --id <session-id>        # 读取该会话的消息内容
  python scripts/find_session.py --id <id> --stats        # token/时间消耗统计

  # 过滤与排序（对标题搜索与正文搜索均生效）
  --project <关键词>     按项目路径（cwd）过滤
  --since 2026-08-01     只保留该日期（含）之后创建的会话
  --until 2026-08-31     只保留该日期（含）之前创建的会话
  --sort time|tokens|turns   排序字段，默认 time
  --max N                最多列出 N 条结果（默认 20）

数据源：
  - 索引 ~/.dsh/storages/session_projcache.json（title/cwd/createdAt/sessionStats/tokenUsage）
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
DEFAULT_GREP_SCAN = 30  # 正文搜索默认只扫最近 N 个会话（全量太慢）


def _out(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def load_sessions() -> dict:
    with open(PROJCACHE, encoding="utf-8") as f:
        return json.load(f)["tables"]["sessions"]


def _row(s, key):
    return (s.get("rows", {}).get(key) or {}).get("val") or {}


def fmt_ts(ms: int) -> str:
    if not ms:
        return "?"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def fmt_tokens(tu) -> str:
    """完整 token 统计：输入（未缓存+缓存读）| 输出 | 缓存写。"""
    if not tu:
        return "?"
    t = tu.get("totals", {})
    uncached = t.get("uncachedInputTokens", 0) or 0
    cache_read = t.get("cacheReadTokens", 0) or 0
    output = t.get("outputTokens", 0) or 0
    cache_write = t.get("cacheWriteTokens", 0) or 0
    return f"输入 {uncached + cache_read} (未缓存 {uncached}+缓存读 {cache_read}) | 输出 {output} | 缓存写 {cache_write}"


def session_hits(
    keyword: str = "",
    project: str = "",
    since: str = "",
    until: str = "",
    sort: str = "time",
) -> list[dict]:
    """从 projcache 检索会话，按标题关键词 + 项目 + 时间范围过滤后排序。"""
    sessions = load_sessions()
    kw = keyword.lower()

    def ts(s) -> int:
        return s.get("identity", {}).get("createdAt", 0)

    def in_range(ms: int) -> bool:
        if not ms:
            return False
        d = datetime.datetime.fromtimestamp(ms / 1000).date()
        if since and d < since:
            return False
        if until and d > until:
            return False
        return True

    hits = []
    for sid, s in sessions.items():
        title = _row(s, "title")
        if isinstance(title, dict):  # val 为空时 _row 返回 {}
            title = ""
        cwd = s.get("identity", {}).get("cwd", "")
        if kw and kw not in title.lower():
            continue
        if project and project.lower() not in cwd.lower():
            continue
        if not in_range(ts(s)):
            continue
        hits.append(
            {
                "id": sid,
                "title": title,
                "cwd": cwd,
                "created": ts(s),
                "stats": _row(s, "sessionStats"),
                "tokenUsage": _row(s, "tokenUsage"),
            }
        )

    if sort == "tokens":
        hits.sort(key=lambda h: ((h["tokenUsage"].get("totals", {}) or {}).get("outputTokens") or 0), reverse=True)
    elif sort == "turns":
        hits.sort(key=lambda h: h["stats"].get("turns", 0), reverse=True)
    else:
        hits.sort(key=lambda h: h["created"], reverse=True)
    return hits


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


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


def messages_with(events: list[dict], kw: str, max_hits: int = 3) -> list[tuple[str, str, str]]:
    """返回匹配正文关键词的消息片段 [(时间, 角色, 文本前300字), ...]。"""
    out = []
    for ev in events:
        t = ev.get("type")
        if t == "user/message":
            text = _text_of(ev.get("data", {}).get("content"))
            role = "USER"
        elif t == "assistant/message":
            text = _text_of(ev.get("data", {}).get("message", {}).get("content"))
            role = "ASSISTANT"
        else:
            continue
        if not text or kw.lower() not in text.lower():
            continue
        out.append((fmt_ts(ev.get("time", 0)), role, text[:300]))
        if len(out) >= max_hits:
            break
    return out


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
    from collections import Counter

    types = Counter(ev.get("type") for ev in events)
    times = [ev.get("time", 0) for ev in events if ev.get("time")]
    duration_min = (max(times) - min(times)) / 60000 if times else 0
    # projcache 聚合统计（token 级数据在 tokenUsage，不在事件流里）
    short = session_id.replace("session-", "")
    stats, token_usage = {}, {}
    for sid, s in load_sessions().items():
        if short in sid:
            stats = _row(s, "sessionStats")
            token_usage = _row(s, "tokenUsage")
            break
    _out(f"会话: {session_id}")
    _out(f"时长: {duration_min:.0f} 分钟 | 回合: {types.get('turn/start', 0)} | 步骤: {types.get('step/start', 0)}")
    _out(f"工具调用: {types.get('tool/call', 0)} | 助手消息: {types.get('assistant/message', 0)} | 压缩: {types.get('compaction/start', 0)} 次")
    if stats:
        _out(
            f"LLM 耗时: {stats.get('llmMs', 0) / 1000:.0f}s | 首 token: {stats.get('ttftMs', 0) / 1000:.0f}s"
        )
    if token_usage:
        _out(f"Token: {fmt_tokens(token_usage)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("keyword", nargs="?", help="标题关键词")
    parser.add_argument("--id", dest="session_id", help="直接按 session-id 操作")
    parser.add_argument("--content", action="store_true", help="搜索后直接读内容")
    parser.add_argument("--stats", action="store_true", help="输出 token/时间统计")
    parser.add_argument("--limit", type=int, default=0, help="内容条数上限（0=全部）")
    parser.add_argument("--grep", dest="grep", help="在会话正文里搜关键词（默认只扫最近 30 个会话）")
    parser.add_argument("--full", action="store_true", help="正文搜索时全量扫描所有会话")
    parser.add_argument("--project", dest="project", default="", help="按项目路径（cwd）过滤")
    parser.add_argument("--since", dest="since", default="", help="只保留该日期（含）之后创建的会话 YYYY-MM-DD")
    parser.add_argument("--until", dest="until", default="", help="只保留该日期（含）之前创建的会话 YYYY-MM-DD")
    parser.add_argument("--sort", dest="sort", default="time", choices=["time", "tokens", "turns"], help="排序字段")
    parser.add_argument("--max", dest="max", type=int, default=20, help="最多列出 N 条结果")
    args = parser.parse_args()

    since = until = None
    try:
        since = datetime.date.fromisoformat(args.since) if args.since else None
        until = datetime.date.fromisoformat(args.until) if args.until else None
    except ValueError:
        _out("日期格式错误，需为 YYYY-MM-DD")
        return 1

    if args.session_id:
        if args.stats:
            show_stats(args.session_id)
        else:
            show_content(args.session_id, args.limit)
        return 0

    # 正文全文搜索
    if args.grep:
        hits = session_hits(project=args.project, since=since, until=until)
        scan = hits if args.full else hits[:DEFAULT_GREP_SCAN]
        _out(f"正文搜索 \"{args.grep}\"：{'全量' if args.full else f'扫描最近 {len(scan)} 个会话（--full 全量）'}\n")
        found = 0
        for h in scan:
            path = find_session_file(h["id"])
            if not path:
                continue
            try:
                events = read_events(path)
            except Exception:
                continue
            msgs = messages_with(events, args.grep)
            if not msgs:
                continue
            found += 1
            _out(f"{fmt_ts(h['created'])} | {h['id']}")
            _out(f"  项目: {h['cwd']} | 标题: {h['title']}")
            for ts, role, text in msgs:
                _out(f"  [{ts}] {role}: {text}")
            _out("")
        _out(f"共 {found} 个会话包含 \"{args.grep}\"")
        return 0

    if not args.keyword:
        parser.print_help()
        return 1

    # 标题搜索
    hits = session_hits(args.keyword, args.project, since, until, args.sort)
    if not hits:
        _out(f"无匹配会话: {args.keyword}")
        return 1
    _out(f"匹配 {len(hits)} 个会话（按{'时间' if args.sort == 'time' else args.sort}倒序，显示前 {min(args.max, len(hits))} 个）:\n")
    for h in hits[: args.max]:
        st = h["stats"]
        _out(
            f"{fmt_ts(h['created'])} | {h['id']}\n"
            f"  项目: {h['cwd']} | 标题: {h['title']}\n"
            f"  回合 {st.get('turns', '?')} | 步骤 {st.get('steps', '?')} | LLM {st.get('llmMs', 0) / 1000:.0f}s | Token: {fmt_tokens(h['tokenUsage'])}"
        )
    if args.content and hits:
        _out("\n" + "=" * 60 + "\n最新匹配的内容:\n")
        show_content(hits[0]["id"], args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
