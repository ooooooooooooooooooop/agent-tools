#!/usr/bin/env node
/**
 * evolution_scan.js — 进化扫描器（Phase 1 交付物 1）
 *
 * 增量扫描 ~/.dsh/sessions 下的会话日志，按已知反模式聚合异常摘要，
 * 追加写入 evolution-inbox（broker topic skills/evolution-inbox 的 workspace）。
 * 只负责"找异常 + 量化证据"，不做根因归纳——归纳与补丁提案归 evolution-proposal skill。
 *
 * 用法:
 *   node scripts/evolution_scan.js                # 增量扫描（默认）
 *   node scripts/evolution_scan.js --force        # 忽略 state，全量重扫
 *   node scripts/evolution_scan.js --dry-run      # 只打印不写 inbox
 *   node scripts/evolution_scan.js --threshold poll=6,retry=4,compaction=3,token=2000000
 *
 * 异常模式（对齐 ~/.dsh/AGENTS.md 治理规则中的已知反模式）:
 *   poll       无 wait 状态查询工具连续/高频调用（request_status/job_list/list_agents 等）
 *   retry      单会话 llm/retry 事件超阈值（重试簇）
 *   compaction 单会话 compaction/start 事件超阈值（上下文压缩风暴）
 *   token      单会话 input+output 总 token 超阈值（成本热点）
 *   repeat    同一工具调用超阈值（含 read 同文件反复、无 wait request_result）
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { zstdDecompressSync } = require("node:zlib");

const ZSTD_MAGIC = 4247762216;

// ---- zstd JSONL 解码（复用既有审计脚本的帧扫描逻辑）----
function scanFrames(buf) {
  const frames = [];
  let offset = 0;
  while (offset + 4 <= buf.length) {
    if (buf.readUInt32LE(offset) !== ZSTD_MAGIC) break;
    const start = offset;
    offset += 4;
    const desc = buf.readUInt8(offset);
    offset += 1;
    const contentSizeFlag = desc >>> 6;
    const singleSegment = (desc & 32) !== 0;
    const checksum = (desc & 4) !== 0;
    const dictFlag = desc & 3;
    const dictBytes = dictFlag === 3 ? 4 : dictFlag;
    const contentSizeBytes = contentSizeFlag === 0 ? (singleSegment ? 1 : 0) : 1 << contentSizeFlag;
    const remHeader = (singleSegment ? 0 : 1) + dictBytes + contentSizeBytes;
    if (buf.length - offset < remHeader) break;
    offset += remHeader;
    for (;;) {
      if (buf.length - offset < 3) return { frames };
      const bh = buf.readUIntLE(offset, 3);
      offset += 3;
      const last = (bh & 1) !== 0;
      const bt = (bh >>> 1) & 3;
      const bs = bh >>> 3;
      if (bt === 3) return { frames };
      const pl = bt === 1 ? 1 : bs;
      if (buf.length - offset < pl) return { frames };
      offset += pl;
      if (last) break;
    }
    if (checksum) {
      if (buf.length - offset < 4) return { frames };
      offset += 4;
    }
    frames.push({ start, end: offset });
  }
  return { frames };
}

function decompress(file) {
  const buf = fs.readFileSync(file);
  const { frames } = scanFrames(buf);
  if (!frames.length) return "";
  let out = "";
  for (const f of frames) {
    try {
      out += zstdDecompressSync(buf.subarray(f.start, f.end)).toString("utf8");
    } catch {}
  }
  return out;
}

// ---- CLI 参数 ----
const args = process.argv.slice(2);
const DEFAULTS = {
  sessionsRoot: path.join(os.homedir(), ".dsh", "sessions"),
  inbox: path.join(os.homedir(), ".agent-broker", "topics", "skills", "evolution-inbox", "workspace", "inbox.jsonl"),
  state: path.join(os.homedir(), ".dsh", ".evolution-inbox", "scan-state.json"),
  force: false,
  dryRun: false,
  thresholds: { poll: 6, retry: 4, compaction: 3, token: 2000000, repeat: 12 },
};

const opts = { ...DEFAULTS };
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--force") opts.force = true;
  else if (a === "--dry-run") opts.dryRun = true;
  else if (a === "--sessions-root") opts.sessionsRoot = args[++i];
  else if (a === "--inbox") opts.inbox = args[++i];
  else if (a === "--state") opts.state = args[++i];
  else if (a === "--threshold") {
    for (const kv of args[++i].split(",")) {
      const [k, v] = kv.split("=");
      if (k in opts.thresholds) opts.thresholds[k] = Number(v);
    }
  } else if (a === "--help") {
    console.log("usage: node scripts/evolution_scan.js [--force] [--dry-run] [--sessions-root DIR] [--inbox FILE] [--state FILE] [--threshold k=v,...]");
    process.exit(0);
  }
}

// ---- 无 wait 状态查询工具集（治理规则模块七/八列举的"轻量状态查询"工具）----
// 注意：长轮询工具（wait_supervisor_event / request_result(wait) / job_output(wait)）是
// 合法等待手段，不在本集合内——本集合只捕"轻量状态查询"（无 wait 的 GET 类工具）。
const POLL_TOOLS = new Set([
  "request_status", "job_list", "list_agents", "get_goal", "get_topic_status",
  "get_cli_requests", "get_codex_requests", "get_antigravity_requests",
  "get_claude_requests", "get_consultation_history", "get_model_defaults",
  "list_managed_claude_supervisors", "get_topic_timeline", "get_request_ledger",
  "get_managed_claude_supervisor", "get_topic_status", "get_latest_context_snapshot",
  "get_work_memory", "get_model_routing_guide", "get_shared_context_stats",
]);
// 长轮询白名单（确认这些工具是合法等待手段，绝不误报为轮询）
const LONG_POLL_TOOLS = new Set([
  "wait_supervisor_event", "request_result", "job_output", "wait_task_receipt",
]);

// ---- 发现会话日志文件 ----
function findSessionLogs(root) {
  const out = [];
  if (!fs.existsSync(root)) return out;
  for (const proj of fs.readdirSync(root)) {
    const projDir = path.join(root, proj);
    let st;
    try { st = fs.statSync(projDir); } catch { continue; }
    if (!st.isDirectory()) continue;
    for (const sd of fs.readdirSync(projDir)) {
      const logFile = path.join(projDir, sd, "session.jsonl.zstd");
      if (!fs.existsSync(logFile)) continue;
      try {
        const fst = fs.statSync(logFile);
        out.push({ logFile, project: proj, sessionId: sd, mtimeMs: fst.mtimeMs, size: fst.size });
      } catch {}
    }
  }
  return out;
}

// ---- 单会话扫描：聚合反模式指标 ----
function scanSession(f) {
  let text;
  try { text = decompress(f.logFile); } catch { return null; }
  const s = {
    id: f.sessionId, project: f.project, file: f.logFile,
    createdAt: null, lastActivity: null, titles: [],
    steps: 0, calls: 0, input: 0, output: 0, cache: 0,
    retries: 0, retryCodes: {}, compactions: 0,
    pollCount: 0, longPollCount: 0, pollSeq: [], toolCalls: {}, readFiles: {}, reads: 0,
    subagents: 0, forks: 0,
  };
  const toolSeq = [];
  for (const l of text.split("\n")) {
    let o;
    try { o = JSON.parse(l); } catch { continue; }
    if (typeof o.time === "number" && (!s.lastActivity || o.time > s.lastActivity)) s.lastActivity = o.time;
    switch (o.type) {
      case "session": s.createdAt = o.createdAt || null; break;
      case "session/title":
        if (o.data && (o.data.title || o.data.name)) s.titles.push(String(o.data.title || o.data.name).slice(0, 80));
        break;
      case "step/start": s.steps++; break;
      case "llm/retry": {
        s.retries++;
        const code = (o.data && o.data.failure && o.data.failure.code) || "?";
        s.retryCodes[code] = (s.retryCodes[code] || 0) + 1;
        break;
      }
      case "assistant/message": {
        const usage = o.data && o.data.usage;
        if (usage && typeof usage.inputTokens === "number") {
          s.calls++;
          s.input += usage.inputTokens || 0;
          s.output += usage.outputTokens || 0;
          s.cache += usage.cacheReadTokens || 0;
        }
        break;
      }
      case "compaction/start": s.compactions++; break;
      case "tool/call": {
        const name = o.data && (o.data.name || (o.data.tool && o.data.tool.name)) || "?";
        s.toolCalls[name] = (s.toolCalls[name] || 0) + 1;
        toolSeq.push(name);
        if (POLL_TOOLS.has(name)) s.pollCount++;
        // 长轮询白名单：合法等待手段，计数作为正面对照（不误报为轮询）
        if (LONG_POLL_TOOLS.has(name)) s.longPollCount++;
        if (name === "read") {
          s.reads++;
          let fp = "";
          try { fp = JSON.parse(o.data.arguments || "{}").file_path || ""; } catch {}
          if (fp) s.readFiles[fp] = (s.readFiles[fp] || 0) + 1;
        }
        if (name === "subagent") s.subagents++;
        if (name === "subagent_fork") s.forks++;
        break;
      }
    }
  }
  // 连续同工具最大游程（无 wait 轮询的近似信号）
  let maxRun = 1, run = 1;
  for (let i = 1; i < toolSeq.length; i++) {
    if (toolSeq[i] === toolSeq[i - 1] && POLL_TOOLS.has(toolSeq[i])) {
      run++;
      if (run > maxRun) maxRun = run;
    } else run = 1;
  }
  s.pollMaxRun = maxRun;
  return s;
}

// ---- 反模式判定（阈值）----
function detectAnomalies(s, th) {
  const anomalies = [];
  if (s.pollCount >= th.poll || s.pollMaxRun >= 5) {
    anomalies.push({
      pattern: "poll",
      severity: s.pollMaxRun >= 5 ? "high" : "medium",
      evidence: { pollCount: s.pollCount, pollMaxRun: s.pollMaxRun, longPollCount: s.longPollCount },
      hint: "无 wait 状态查询高频/连续出现，疑似轮询反模式；应改为单次长轮询（wait=true / wait_supervisor_event / request_result）",
    });
  }
  if (s.retries >= th.retry) {
    anomalies.push({
      pattern: "retry",
      severity: s.retries >= th.retry * 2 ? "high" : "medium",
      evidence: { retries: s.retries, codes: s.retryCodes, calls: s.calls },
      hint: "重试簇：检查 provider×错误码根因（dsh-retry-analysis.js），或是否配置了不重试码",
    });
  }
  if (s.compactions >= th.compaction) {
    anomalies.push({
      pattern: "compaction",
      severity: "medium",
      evidence: { compactions: s.compactions, steps: s.steps },
      hint: "上下文压缩风暴：检查是否有大文件全量 read 或子代理报告全文 splice 回主上下文",
    });
  }
  const total = s.input + s.output;
  if (total >= th.token) {
    anomalies.push({
      pattern: "token",
      severity: "high",
      evidence: { inputTokens: s.input, outputTokens: s.output, cacheReadTokens: s.cache, totalTokens: total },
      hint: "token 成本热点：单会话超阈值，检查重复派发/全量 fork/大文件重复读取",
    });
  }
  for (const [tool, count] of Object.entries(s.toolCalls)) {
    if (count >= th.repeat) {
      anomalies.push({
        pattern: "repeat",
        severity: "low",
        evidence: { tool, count },
        hint: `工具 ${tool} 单会话调用 ${count} 次，检查是否重复同一动作`,
      });
    }
  }
  for (const [fp, count] of Object.entries(s.readFiles)) {
    if (count >= 3) {
      anomalies.push({
        pattern: "read-repeat",
        severity: "low",
        evidence: { file: fp, count },
        hint: "同一文件反复 read，应一次读取后用 offset/limit 窗口续读，或委派探查 subagent",
      });
    }
  }
  return anomalies;
}

// ---- 主流程 ----
function main() {
  const files = findSessionLogs(opts.sessionsRoot);
  let state = { version: 1, seen: {} };
  try { state = JSON.parse(fs.readFileSync(opts.state, "utf8")); } catch {}

  // --force 时按 sessionId 去重：已存在 inbox 的会话不重复追加
  const knownSessions = new Set();
  if (opts.force && fs.existsSync(opts.inbox)) {
    for (const l of fs.readFileSync(opts.inbox, "utf8").split("\n")) {
      try { const e = JSON.parse(l); if (e.sessionId) knownSessions.add(e.sessionId); } catch {}
    }
  }

  const newEntries = [];
  const scanned = [];
  for (const f of files) {
    const key = f.logFile;
    const prev = state.seen[key];
    if (!opts.force && prev && prev.mtimeMs === f.mtimeMs && prev.size === f.size) continue;
    const s = scanSession(f);
    if (!s) continue;
    scanned.push(f.logFile);
    const anomalies = detectAnomalies(s, opts.thresholds);
    state.seen[key] = { mtimeMs: f.mtimeMs, size: f.size };
    if (!anomalies.length) continue;
    if (knownSessions.has(s.id)) continue;
    newEntries.push({
      schema: "evolution-inbox/v1",
      createdAt: new Date().toISOString(),
      sessionId: s.id,
      project: s.project,
      title: (s.titles[0] || "").slice(0, 80),
      lastActivity: s.lastActivity ? new Date(s.lastActivity).toISOString() : null,
      anomalies,
      metrics: {
        steps: s.steps, calls: s.calls,
        inputTokens: s.input, outputTokens: s.output, cacheReadTokens: s.cache,
        retries: s.retries, compactions: s.compactions,
        pollCount: s.pollCount, subagents: s.subagents, forks: s.forks,
      },
      status: "new",
    });
  }

  // 追加写 inbox（JSONL）
  if (newEntries.length && !opts.dryRun) {
    fs.mkdirSync(path.dirname(opts.inbox), { recursive: true });
    fs.appendFileSync(opts.inbox, newEntries.map((e) => JSON.stringify(e)).join("\n") + "\n", "utf8");
    fs.mkdirSync(path.dirname(opts.state), { recursive: true });
    fs.writeFileSync(opts.state, JSON.stringify(state, null, 2), "utf8");
  } else if (newEntries.length && opts.dryRun) {
    // dry-run 不落盘，但展示将写入的内容
  }

  console.log(`evolution_scan: scanned ${scanned.length} new/changed session log(s), ${newEntries.length} anomaly entry(ies)`);
  for (const e of newEntries) {
    const pats = e.anomalies.map((a) => `${a.pattern}(${a.severity})`).join(",");
    console.log(`  + [${e.sessionId.slice(0, 8)}] ${e.title.slice(0, 40)} -> ${pats}`);
  }
  if (opts.dryRun && newEntries.length) {
    console.log(`  (dry-run: would append to ${opts.inbox})`);
  }
  return newEntries.length > 0 ? 0 : 0;
}

process.exit(main());
