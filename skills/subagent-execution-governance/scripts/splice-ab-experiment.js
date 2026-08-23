#!/usr/bin/env node
/**
 * Splice A/B 缓存实验（Splice A/B Cache Experiment）
 *
 * 验证"子代理报告全量 splice 回主上下文"与"结构化摘要化"两种策略的
 * 上下文体积差异。读取一个 DSH 会话日志，找出所有注入主上下文的
 * 子代理报告（source.kind === 'subagent-report' / 'subagent-settled'），
 * 对每条报告分别计算：
 *   - A 分支（现状）：全量文本进入主上下文
 *   - B 分支（治理后）：经结构化摘要（与 subagent-splice-summarizer 插件
 *     相同逻辑）后再进入
 * 输出总字符数、token 估算（中文 ≈1.5 chars/token 保守估算）、节省比例，
 * 以及"若每条报告都摘要化，主上下文可瘦身多少"的结论。
 *
 * 用法：
 *   node splice-ab-experiment.js <session.jsonl.zstd 路径>
 *   node splice-ab-experiment.js <会话 ID>            # 在 ~/.dsh/sessions 下自动定位
 *
 * 依赖：Node >= 22（node:zlib 内置 zstd 支持）
 */

const { zstdDecompressSync } = require("node:zlib");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const ZSTD_MAGIC = 4247762216; // 0xFD2FB528 LE

/* ---------- 多帧 zstd 扫描（与 session-discipline-audit.js 同款） ---------- */

function scanFrames(buffer) {
  const frames = [];
  let offset = 0;
  while (offset < buffer.length) {
    if (buffer.length - offset < 4) break;
    if (buffer.readUInt32LE(offset) !== ZSTD_MAGIC) {
      let next = -1;
      for (let i = offset + 1; i < buffer.length - 3; i++) {
        if (buffer.readUInt32LE(i) === ZSTD_MAGIC) { next = i; break; }
      }
      if (next < 0) break;
      frames.push({ start: offset, end: next });
      offset = next;
      continue;
    }
    const fhd = buffer[offset + 4];
    const singleSegment = (fhd & 0x20) !== 0;
    let pos = offset + 5;
    if (singleSegment) {
      const desc = fhd & 0x1f;
      if (desc >= 8 && desc < 16) pos += 1;
      else if (desc >= 16 && desc < 24) pos += 2;
      else if (desc >= 24 && desc < 32) pos += 4;
      else if (desc >= 32) pos += 8;
    } else {
      const desc = fhd & 0x3f;
      if (desc === 0) throw new Error("unknown frame content size");
      else if (desc < 8) pos += 1;
      else if (desc < 16) pos += 2;
      else if (desc < 24) pos += 4;
      else pos += 8;
      pos += 1; // window descriptor
    }
    if ((fhd & 0x01) !== 0) {
      const d = buffer[pos];
      pos += (d & 0x0f) === 0 ? 1 : (d & 0x0f) === 1 ? 2 : 4;
    }
    let next = -1;
    for (let i = pos; i < buffer.length - 3; i++) {
      if (buffer.readUInt32LE(i) === ZSTD_MAGIC) { next = i; break; }
    }
    const end = next < 0 ? buffer.length : next;
    frames.push({ start: offset, end });
    if (next < 0) break;
    offset = next;
  }
  return frames;
}

function decodeSession(filePath) {
  const buf = fs.readFileSync(filePath);
  const frames = scanFrames(buf);
  let all = "";
  for (const f of frames) {
    try { all += zstdDecompressSync(buf.subarray(f.start, f.end)).toString("utf8"); }
    catch (e) { /* skip corrupt frame */ }
  }
  const events = [];
  for (const line of all.split("\n")) {
    if (!line.trim()) continue;
    try { events.push(JSON.parse(line)); } catch (e) {}
  }
  return events;
}

/* ---------- 定位会话文件 ---------- */

function resolveSessionFile(arg) {
  if (fs.existsSync(arg) && fs.statSync(arg).isFile()) return arg;
  const root = path.join(os.homedir(), ".dsh", "sessions");
  if (!fs.existsSync(root)) return null;
  for (const workspace of fs.readdirSync(root)) {
    const wsDir = path.join(root, workspace);
    if (!fs.statSync(wsDir).isDirectory()) continue;
    const target = path.join(wsDir, arg, "session.jsonl.zstd");
    if (fs.existsSync(target)) return target;
  }
  return null;
}

/* ---------- 结构化摘要（与 subagent-splice-summarizer 插件一致） ---------- */

function buildSummary(raw) {
  const lines = raw.split("\n");
  const kept = [];
  const tagPattern = /^(###|##|\*\*|\-)\s*(status|changed|validation|deviations|blocker|artifact|contract|goal|scope|must not|exit|next)/i;
  const seen = new Set();
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const match = tagPattern.exec(line);
    if (match) {
      const key = match[2].toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        kept.push(line);
        for (let j = 1; j <= 3 && i + j < lines.length; j++) {
          const next = lines[i + j];
          if (tagPattern.test(next) || next.trim() === "") break;
          kept.push(next);
        }
        kept.push("");
      }
    }
  }
  if (kept.length < 5) {
    const maxLen = 4000;
    if (raw.length <= maxLen) return raw;
    return raw.slice(0, 2000) + "\n\n...[中间 " + (raw.length - 4000) + " 字符已截断]...\n\n" + raw.slice(-2000);
  }
  const result = "[摘要] 子代理报告结构摘要：\n" + kept.join("\n").trim();
  if (result.length > 6000) {
    return result.slice(0, 3000) + "\n\n...[长摘要已截断]...\n\n" + result.slice(-2000);
  }
  return result;
}

/* ---------- 估算 token（中文/英文混合保守：1.5 chars/token） ---------- */

function estimateTokens(chars) {
  return Math.round(chars / 1.5);
}

/* ---------- 主流程 ---------- */

function main() {
  const arg = process.argv[2];
  if (!arg) {
    console.error("用法: node splice-ab-experiment.js <session.jsonl.zstd 路径 | 会话 ID>");
    process.exit(1);
  }
  const file = resolveSessionFile(arg);
  if (!file) {
    console.error("找不到会话文件: " + arg);
    process.exit(1);
  }
  const events = decodeSession(file);
  console.log("=== Splice A/B 缓存实验报告 ===");
  console.log("文件: " + file);
  console.log("事件总数: " + events.length);

  // 收集注入主上下文的子代理报告（user/message 且 source.kind 为 subagent-report/settled）
  const spliced = [];
  for (const e of events) {
    if (e.type !== "user/message") continue;
    const src = e.data && e.data.source;
    if (!src) continue;
    if (src.kind !== "subagent-report" && src.kind !== "subagent-settled") continue;
    const blocks = (e.data && e.data.content) || [];
    const text = blocks
      .filter((b) => b.type === "text")
      .map((b) => b.text || "")
      .join("");
    spliced.push({ seq: e.seq, turn: e.data.turn, step: e.data.step, kind: src.kind, text });
  }

  console.log("注入主上下文的子代理报告条数: " + spliced.length);

  let aChars = 0, bChars = 0;
  const perReport = [];
  for (const s of spliced) {
    const b = buildSummary(s.text);
    perReport.push({
      seq: s.seq,
      turn: s.turn,
      step: s.step,
      kind: s.kind,
      aChars: s.text.length,
      bChars: b.length,
      saved: s.text.length - b.length,
    });
    aChars += s.text.length;
    bChars += b.length;
  }

  perReport.sort((x, y) => y.saved - x.saved);

  console.log("\n--- 汇总 ---");
  console.log("A（现状·全量注入）: " + aChars.toLocaleString() + " 字符 ≈ " + estimateTokens(aChars).toLocaleString() + " tokens");
  console.log("B（治理·摘要化）:   " + bChars.toLocaleString() + " 字符 ≈ " + estimateTokens(bChars).toLocaleString() + " tokens");
  const saved = aChars - bChars;
  const pct = aChars > 0 ? ((saved / aChars) * 100).toFixed(1) : "0";
  console.log("节省: " + saved.toLocaleString() + " 字符 ≈ " + estimateTokens(saved).toLocaleString() + " tokens (" + pct + "%)");

  // 按轮聚合：每轮注入量
  const byTurn = {};
  for (const p of perReport) {
    const k = p.turn == null ? "?" : p.turn;
    if (!byTurn[k]) byTurn[k] = { aChars: 0, bChars: 0, count: 0 };
    byTurn[k].aChars += p.aChars;
    byTurn[k].bChars += p.bChars;
    byTurn[k].count++;
  }
  console.log("\n--- 按轮聚合（top 5） ---");
  Object.keys(byTurn)
    .sort((x, y) => byTurn[y].aChars - byTurn[x].aChars)
    .slice(0, 5)
    .forEach((k) => {
      const t = byTurn[k];
      console.log(
        "轮 " + k + ": " + t.count + " 条注入, A=" + t.aChars.toLocaleString() + " chars ≈ " +
        estimateTokens(t.aChars).toLocaleString() + " tok, B=" + t.bChars.toLocaleString() +
        " chars ≈ " + estimateTokens(t.bChars).toLocaleString() + " tok"
      );
    });

  console.log("\n--- 节省最多的单条报告（top 5） ---");
  perReport.slice(0, 5).forEach((p) => {
    console.log(
      "seq=" + p.seq + " (turn " + p.turn + " step " + p.step + ", " + p.kind + "): " +
      p.aChars.toLocaleString() + " → " + p.bChars.toLocaleString() +
      " chars, 省 " + p.saved.toLocaleString() + " chars ≈ " + estimateTokens(p.saved).toLocaleString() + " tok"
    );
  });

  // 结论
  console.log("\n--- 结论 ---");
  if (spliced.length === 0) {
    console.log("该会话没有子代理报告注入，无法做 A/B 对比。");
  } else {
    const avg = estimateTokens(aChars / spliced.length);
    console.log(
      "每条报告平均注入 " + avg.toLocaleString() + " tokens；摘要化后平均 " +
      estimateTokens(bChars / spliced.length).toLocaleString() + " tokens。"
    );
    console.log(
      "若全量注入是造成 cache=0 大输入步的元凶之一（相关性证据），摘要化可让每轮主上下文" +
      "减少约 " + estimateTokens(saved).toLocaleString() + " tokens 的重复前缀体积，从而降低" +
      "后续请求的 cache miss 面。注意：这是体积层面的结构性证据，" +
      "缓存命中率还需 A/B 重放实验实测。"
    );
  }
}

main();
