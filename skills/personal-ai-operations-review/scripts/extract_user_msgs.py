# 从 DSH 事件型 session.jsonl.zstd 提取真实用户消息（只读）
# 产物写入仓库运行输出层 output/pref-calibration/（不写入 skill 包、不进发布集）。
# 依赖：zstandard（python -m pip install zstandard）。
import json, io, sys
from pathlib import Path
import zstandard as zs

SRC = Path.home() / ".dsh" / "sessions"
REPO = Path(__file__).resolve().parents[3]
OUTDIR = REPO / "output" / "pref-calibration"
OUTDIR.mkdir(parents=True, exist_ok=True)
OUT = OUTDIR / "user_messages.jsonl"
IDX = OUTDIR / "session_index.jsonl"

def stream_lines(path):
    dctx = zs.ZstdDecompressor()
    with open(path, "rb") as fh:
        with dctx.stream_reader(fh) as reader:
            t = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
            for line in t:
                yield line

count_sessions = 0
count_top = 0
count_msgs = 0
with open(OUT, "w", encoding="utf-8") as out, open(IDX, "w", encoding="utf-8") as idx:
    for f in SRC.rglob("*.jsonl.zstd"):
        count_sessions += 1
        meta = {}
        title = ""
        msgs = []
        for line in stream_lines(f):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ty = rec.get("type")
            if ty == "session":
                meta = rec
            elif ty == "session/title":
                title = (rec.get("data") or {}).get("title", title)
            elif ty == "user/message":
                d = rec.get("data") or {}
                src = d.get("source") or {}
                if src.get("kind") != "user":
                    continue
                parts = [c.get("text","") for c in (d.get("content") or []) if isinstance(c, dict) and c.get("type")=="text"]
                text = "\n".join(p for p in parts if p).strip()
                if len(text) < 8:
                    continue
                msgs.append({"seq": rec.get("seq"), "time": rec.get("time"), "text": text})
        if not meta:
            continue
        depth = meta.get("delegationDepth", 0)
        origin = meta.get("origin", "")
        is_top = (depth == 0 and origin != "subagent")
        idx.write(json.dumps({"session": meta.get("id"), "createdAt": meta.get("createdAt"),
                              "cwd": meta.get("cwd"), "origin": origin, "depth": depth,
                              "title": title, "n_user_msgs": len(msgs), "top": is_top}, ensure_ascii=False) + "\n")
        if not is_top:
            continue
        count_top += 1
        for m in msgs:
            out.write(json.dumps({"session": meta.get("id"), "cwd": meta.get("cwd"), "title": title,
                                  "time": m["time"], "text": m["text"]}, ensure_ascii=False) + "\n")
            count_msgs += 1

print(f"sessions={count_sessions} top_level={count_top} user_msgs={count_msgs}")
