#!/usr/bin/env node
/**
 * 会话纪律体检工具（Session Discipline Audit）
 *
 * 解码 DSH 会话日志（多帧 zstd 的 session.jsonl.zstd），输出一份纪律体检报告：
 *  - 主会话/子代理模型路由
 *  - 每轮 token 消耗（输入/输出/缓存读取）
 *  - 子代理清单：模型、轮次、工具调用、空转信号
 *  - 催收成本：send_message / interrupt_agent 次数
 *  - 缓存异常：cache=0 且输入 > 阈值的高价步骤
 *  - 短轮询残留：job_output 短超时调用
 *  - 大文件全量读风险：read 不带 offset/limit
 *
 * 用法：
 *   node session-discipline-audit.js <session.jsonl.zstd 路径>
 *   node session-discipline-audit.js <会话 ID>            # 在 ~/.dsh/sessions 下自动定位
 *
 * 依赖：Node >= 22（node:zlib 内置 zstd 支持）
 */

const { zstdDecompressSync } = require("node:zlib");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const ZSTD_MAGIC = 4247762216; // 0xFD2FB528 LE
const BIG_CACHE_MISS_INPUT = 10000; // 单步未命中缓存输入阈值
const SHORT_POLL_MS = 5000; // job_output 短超时阈值（ms）

/* ---------- 多帧 zstd 扫描 ---------- */

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

/* ---------- 分析 ---------- */

function analyze(events) {
  const report = {
    header: null,
    models: new Set(),
    subagentModels: new Map(), // id -> [models]
    perTurn: {}, // turn -> {in, out, cache, steps}
    toolCalls: [],
    subagentStarts: [], // {seq, turn, step, args}
    sendMessages: [],
    interrupts: [],
    cacheMissSteps: [],
    shortPolls: [],
    fullReads: [],
    totals: { in: 0, out: 0, cache: 0 },
  };

  for (const e of events) {
    if (e.type === "session") report.header = e;
    else if (e.type === "request/header") {
      const cfg = e.data?.header?.config;
      if (cfg) report.models.add(`${cfg.provider}/${cfg.model}`);
    } else if (e.type === "request/context") {
      if (e.data?.model) report.models.add(`${e.data.provider}/${e.data.model}`);
    } else if (e.type === "assistant/chunk" && e.data?.chunk?.type === "usage") {
      const u = e.data.chunk.usage || {};
      const turn = e.data.turn;
      const step = e.data.step;
      if (!report.perTurn[turn]) report.perTurn[turn] = { in: 0, out: 0, cache: 0, steps: 0 };
      report.perTurn[turn].in += u.inputTokens || 0;
      report.perTurn[turn].out += u.outputTokens || 0;
      report.perTurn[turn].cache += u.cacheReadTokens || 0;
      report.perTurn[turn].steps += 1;
      report.totals.in += u.inputTokens || 0;
      report.totals.out += u.outputTokens || 0;
      report.totals.cache += u.cacheReadTokens || 0;
      if ((u.cacheReadTokens || 0) === 0 && (u.inputTokens || 0) > BIG_CACHE_MISS_INPUT) {
        report.cacheMissSteps.push({ turn, step, in: u.inputTokens, out: u.outputTokens });
      }
    } else if (e.type === "tool/call") {
      const d = e.data || {};
      const name = d.name;
      const call = { seq: e.seq, time: e.time, turn: d.turn, step: d.step, name };
      try { call.args = typeof d.arguments === "string" ? d.arguments : JSON.stringify(d.arguments || {}); } catch (err) { call.args = ""; }
      report.toolCalls.push(call);

      if (name === "subagent") {
        let a = {};
        try { a = JSON.parse(call.args); } catch (err) {}
        report.subagentStarts.push({ seq: e.seq, turn: d.turn, step: d.step, bg: a.run_in_background, desc: a.description, promptLen: (a.prompt || "").length });
      } else if (name === "send_message") {
        report.sendMessages.push(call);
      } else if (name === "interrupt_agent") {
        report.interrupts.push(call);
      } else if (name === "job_output") {
        let a = {};
        try { a = JSON.parse(call.args); } catch (err) {}
        if (a.wait === true && (a.timeout_ms || 0) < SHORT_POLL_MS) {
          report.shortPolls.push({ seq: e.seq, turn: d.turn, step: d.step, timeout_ms: a.timeout_ms, job: a.job_id });
        }
      } else if (name === "read") {
        let a = {};
        try { a = JSON.parse(call.args); } catch (err) {}
        if (a.offset === undefined || a.limit === undefined) {
          report.fullReads.push({ seq: e.seq, turn: d.turn, step: d.step, file: a.file_path });
        }
      }
    }
  }
  return report;
}

function findSessionFile(arg) {
  if (fs.existsSync(arg) && fs.statSync(arg).isFile()) return arg;
  // try ~/.dsh/sessions/**/<id>/session.jsonl.zstd
  const roots = [path.join(os.homedir(), ".dsh", "sessions")];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    const hit = findFileRecursive(root, arg);
    if (hit) return hit;
  }
  return null;
}

function findFileRecursive(root, id) {
  let result = null;
  try {
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
      if (result) break;
      const full = path.join(root, entry.name);
      if (entry.isDirectory()) {
        result = findFileRecursive(full, id);
      } else if (entry.name === "session.jsonl.zstd" && entry.name.includes("jsonl") && entry.name.includes("zstd")) {
        // match by parent dir name containing the id
        if (path.basename(path.dirname(full)).includes(id)) result = full;
      }
    }
  } catch (e) {}
  return result;
}

/* ---------- 报告 ---------- */

function render(report, filePath) {
  const L = [];
  const push = (s = "") => L.push(s);

  push(`=== 会话纪律体检报告 ===`);
  push(`文件: ${filePath}`);
  push(`会话: ${report.header?.id || "unknown"} | preset: ${report.header?.agentPreset || "-"}`);
  push(`模型: ${[...report.models].join(", ") || "无 request/header 记录"}`);
  push(`工具调用总数: ${report.toolCalls.length} | 子代理派发: ${report.subagentStarts.length}`);
  push(`催收成本: send_message ${report.sendMessages.length} 次 | interrupt_agent ${report.interrupts.length} 次`);
  push(``);
  push(`--- Token 消耗（按轮） ---`);
  push(`turn | in | out | cacheRead | steps`);
  for (const [t, v] of Object.entries(report.perTurn).sort((a, b) => a[0] - b[0])) {
    push(`${t} | ${v.in.toLocaleString()} | ${v.out.toLocaleString()} | ${v.cache.toLocaleString()} | ${v.steps}`);
  }
  push(`TOTAL | ${report.totals.in.toLocaleString()} | ${report.totals.out.toLocaleString()} | ${report.totals.cache.toLocaleString()}`);

  push(``);
  push(`--- 子代理派发 ---`);
  for (const s of report.subagentStarts) {
    push(`seq=${s.seq} turn=${s.turn}.${s.step} bg=${s.bg} desc=${s.desc} promptLen=${s.promptLen}`);
  }

  if (report.cacheMissSteps.length) {
    push(``);
    push(`--- 缓存异常：cache=0 且输入 > ${BIG_CACHE_MISS_INPUT} ---`);
    for (const c of report.cacheMissSteps) push(`turn=${c.turn} step=${c.step} in=${c.in.toLocaleString()}`);
  }

  if (report.shortPolls.length) {
    push(``);
    push(`--- 短轮询残留（job_output 超时 < ${SHORT_POLL_MS}ms）---`);
    for (const p of report.shortPolls) push(`seq=${p.seq} turn=${p.turn}.${p.step} job=${p.job} timeout=${p.timeout_ms}ms`);
  }

  if (report.fullReads.length) {
    push(``);
    push(`--- 全量读风险（read 未带 offset/limit）---`);
    for (const r of report.fullReads) push(`seq=${r.seq} turn=${r.turn}.${r.step} file=${r.file}`);
  }

  push(``);
  push(`=== 体检项 ===`);
  const checks = [];
  checks.push(report.sendMessages.length > 10 ? `⚠ send_message 催促过多（${report.sendMessages.length} 次）` : `✓ 催收成本可控（${report.sendMessages.length} 次）`);
  checks.push(report.interrupts.length > 3 ? `⚠ interrupt 频繁（${report.interrupts.length} 次）` : `✓ 中断可控（${report.interrupts.length} 次）`);
  checks.push(report.cacheMissSteps.length > 1 ? `⚠ 大输入未命中缓存 ${report.cacheMissSteps.length} 步（疑似大量 splice 注入）` : `✓ 缓存命中正常`);
  checks.push(report.shortPolls.length > 0 ? `⚠ 短轮询 ${report.shortPolls.length} 次` : `✓ 无短轮询`);
  checks.push(report.fullReads.length > 0 ? `⚠ 全量读 ${report.fullReads.length} 次` : `✓ 读取均带窗口`);
  for (const c of checks) push(c);

  return L.join("\n");
}

/* ---------- main ---------- */

const arg = process.argv[2];
if (!arg) {
  console.error("用法: node session-discipline-audit.js <session.jsonl.zstd 路径 | 会话 ID>");
  process.exit(1);
}
const file = findSessionFile(arg);
if (!file) {
  console.error(`未找到会话文件: ${arg}`);
  process.exit(1);
}
const events = decodeSession(file);
const report = analyze(events);
process.stdout.write(render(report, file) + "\n");
