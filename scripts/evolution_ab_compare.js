#!/usr/bin/env node
/**
 * evolution_ab_compare.js — 规则/策略变更前后 A/B 对比（Phase 2 交付物 3）
 *
 * 以某个策略生效时刻为分界，对 ~/.dsh/sessions 下的会话日志做前后聚合对比
 * （重试率 / input+cache token / 轮询会话数 / fork 数 / compaction 数），
 * 输出结构化对比报告 JSON 到 evolution-inbox 的 ab-reports/ 目录，
 * 并追加一条 inbox 条目（pattern: ab-compare），完成"变更 → 测量 → 回写"闭环。
 *
 * 用法:
 *   node scripts/evolution_ab_compare.js --policy-ms <epoch_ms>        # 按时间戳分界
 *   node scripts/evolution_ab_compare.js --policy-iso "2026-08-22T04:10:00+08:00"
 *   node scripts/evolution_ab_compare.js --policy-ms 1787... --label "retry-policy-v2"
 *   node scripts/evolution_ab_compare.js --dry-run                    # 只打印不写文件
 *
 * 复用 dsh-event-time-audit.js 的聚合口径（事件时间 >= policy 归为 after）。
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { zstdDecompressSync } = require("node:zlib");

const ZSTD_MAGIC = 4247762216;

// ---- zstd JSONL 解码（复用既有审计脚本）----
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

// ---- 无 wait 状态查询工具集（与 evolution_scan.js 保持一致）----
const POLL_TOOLS = new Set([
  "request_status", "job_list", "list_agents", "get_goal", "get_topic_status",
  "get_cli_requests", "get_codex_requests", "get_antigravity_requests",
  "get_claude_requests", "get_consultation_history", "get_model_defaults",
  "list_managed_claude_supervisors", "get_topic_timeline", "get_request_ledger",
]);

// ---- CLI 参数 ----
const args = process.argv.slice(2);
const opts = {
  sessionsRoot: path.join(os.homedir(), ".dsh", "sessions"),
  inbox: path.join(os.homedir(), ".agent-broker", "topics", "skills", "evolution-inbox", "workspace", "inbox.jsonl"),
  policyMs: null,
  label: "policy-change",
  dryRun: false,
};
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--policy-ms") opts.policyMs = Number(args[++i]);
  else if (a === "--policy-iso") opts.policyMs = Date.parse(args[++i]);
  else if (a === "--label") opts.label = args[++i];
  else if (a === "--sessions-root") opts.sessionsRoot = args[++i];
  else if (a === "--inbox") opts.inbox = args[++i];
  else if (a === "--dry-run") opts.dryRun = true;
  else if (a === "--help") {
    console.log("usage: node scripts/evolution_ab_compare.js --policy-ms <epoch_ms>|--policy-iso <ISO> [--label NAME] [--dry-run]");
    process.exit(0);
  }
}
if (!opts.policyMs || Number.isNaN(opts.policyMs)) {
  console.error("ERROR: --policy-ms or --policy-iso is required");
  process.exit(2);
}

// ---- 会话扫描与聚合（口径与 dsh-event-time-audit.js 一致）----
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
      out.push({ logFile, project: proj, sessionId: sd });
    }
  }
  return out;
}

function scanSession(f) {
  let text;
  try { text = decompress(f.logFile); } catch { return null; }
  const s = {
    id: f.sessionId, project: f.project,
    lastActivity: null, titles: [],
    steps: 0, calls: 0, input: 0, output: 0, cache: 0,
    retries: 0, retryCodes: {}, compactions: 0,
    pollCount: 0, pollMaxRun: 0, subagents: 0, forks: 0,
  };
  const toolSeq = [];
  for (const l of text.split("\n")) {
    let o;
    try { o = JSON.parse(l); } catch { continue; }
    if (typeof o.time === "number" && (!s.lastActivity || o.time > s.lastActivity)) s.lastActivity = o.time;
    switch (o.type) {
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
        toolSeq.push(name);
        if (POLL_TOOLS.has(name)) s.pollCount++;
        if (name === "subagent") s.subagents++;
        if (name === "subagent_fork") s.forks++;
        break;
      }
    }
  }
  let run = 1, maxRun = 1;
  for (let i = 1; i < toolSeq.length; i++) {
    if (toolSeq[i] === toolSeq[i - 1] && POLL_TOOLS.has(toolSeq[i])) { run++; if (run > maxRun) maxRun = run; }
    else run = 1;
  }
  s.pollMaxRun = maxRun;
  return s;
}

function aggregate(sessions) {
  const r = {
    sessions: sessions.length, calls: 0, retries: 0,
    inputTokens: 0, outputTokens: 0, cacheReadTokens: 0,
    compactions: 0, pollSessions: 0, pollCalls: 0,
    forks: 0, subagents: 0, retryCodes: {},
  };
  for (const s of sessions) {
    r.calls += s.calls; r.retries += s.retries;
    r.inputTokens += s.input; r.outputTokens += s.output; r.cacheReadTokens += s.cache;
    r.compactions += s.compactions; r.pollCalls += s.pollCount;
    r.forks += s.forks; r.subagents += s.subagents;
    if (s.pollMaxRun >= 5 || s.pollCount >= 6) r.pollSessions++;
    for (const [k, v] of Object.entries(s.retryCodes)) r.retryCodes[k] = (r.retryCodes[k] || 0) + v;
  }
  r.retryRate = r.calls ? +(100 * r.retries / r.calls).toFixed(2) : 0;
  r.avgTokensPerSession = r.sessions ? Math.round((r.inputTokens + r.outputTokens) / r.sessions) : 0;
  r.cacheHitRate = (r.inputTokens + r.cacheReadTokens)
    ? +(100 * r.cacheReadTokens / (r.inputTokens + r.cacheReadTokens)).toFixed(2)
    : 0;
  return r;
}

function delta(before, after) {
  const keys = ["calls", "retries", "retryRate", "inputTokens", "outputTokens",
    "cacheReadTokens", "compactions", "pollSessions", "pollCalls", "forks", "subagents", "avgTokensPerSession"];
  const d = {};
  for (const k of keys) {
    if (typeof after[k] !== "number" || typeof before[k] !== "number") continue;
    const change = after[k] - before[k];
    const pct = before[k] !== 0 ? +((100 * change) / before[k]).toFixed(2) : (change !== 0 ? 100 : 0);
    d[k] = { before: before[k], after: after[k], delta: change, pct };
  }
  return d;
}

function verdict(d) {
  // 期望方向：重试率、轮询、compaction、fork 越低越好；cache 命中率越高越好
  const improving = [];
  const regressing = [];
  const check = (key, lowerBetter, name) => {
    const row = d[key];
    if (!row) return;
    if (Math.abs(row.pct) < 5) return;
    const better = lowerBetter ? row.delta < 0 : row.delta > 0;
    (better ? improving : regressing).push(`${name} ${row.pct > 0 ? "+" : ""}${row.pct}%`);
  };
  check("retryRate", true, "retry-rate");
  check("pollCalls", true, "poll-calls");
  check("compactions", true, "compactions");
  check("forks", true, "forks");
  check("avgTokensPerSession", true, "avg-tokens/session");
  return { verdict: regressing.length ? "REGRESSION" : (improving.length ? "IMPROVED" : "NEUTRAL"), improving, regressing };
}

// ---- 主流程 ----
function main() {
  const files = findSessionLogs(opts.sessionsRoot);
  const before = [];
  const after = [];
  for (const f of files) {
    const s = scanSession(f);
    if (!s || s.lastActivity === null) continue;
    if (s.lastActivity >= opts.policyMs) after.push(s);
    else before.push(s);
  }
  const aggBefore = aggregate(before);
  const aggAfter = aggregate(after);
  const d = delta(aggBefore, aggAfter);
  const v = verdict(d);

  const report = {
    schema: "ab-compare/v1",
    label: opts.label,
    policyMs: opts.policyMs,
    policyIso: new Date(opts.policyMs).toISOString(),
    generatedAt: new Date().toISOString(),
    sessions: { before: before.length, after: after.length },
    before: aggBefore,
    after: aggAfter,
    delta: d,
    verdict: v,
  };

  if (opts.dryRun) {
    console.log(JSON.stringify(report, null, 2));
    return 0;
  }

  const inboxDir = path.dirname(opts.inbox);
  const reportsDir = path.join(inboxDir, "ab-reports");
  fs.mkdirSync(reportsDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const reportFile = path.join(reportsDir, `${opts.label}-${stamp}.json`);
  fs.writeFileSync(reportFile, JSON.stringify(report, null, 2), "utf8");

  // 回写 inbox 条目（pattern: ab-compare），供 evolution-proposal 消费
  const entry = {
    schema: "evolution-inbox/v1",
    createdAt: new Date().toISOString(),
    sessionId: `ab-compare:${opts.label}`,
    project: "evolution-ab",
    title: `A/B 对比：${opts.label}（${v.verdict}）`,
    lastActivity: new Date(opts.policyMs).toISOString(),
    anomalies: [{
      pattern: "ab-compare",
      severity: v.verdict === "REGRESSION" ? "high" : "medium",
      evidence: {
        reportFile,
        verdict: v.verdict,
        improving: v.improving,
        regressing: v.regressing,
        sessionsBefore: before.length,
        sessionsAfter: after.length,
      },
      hint: "策略变更前后对比报告已生成，按 evolution-proposal 评估是否需要回滚或继续调整",
    }],
    metrics: {
      retryRate: aggAfter.retryRate,
      pollCalls: aggAfter.pollCalls,
      avgTokensPerSession: aggAfter.avgTokensPerSession,
    },
    status: "new",
  };
  fs.appendFileSync(opts.inbox, JSON.stringify(entry) + "\n", "utf8");

  console.log(`ab-compare: ${v.verdict} (before=${before.length} sessions, after=${after.length})`);
  console.log(`  report: ${reportFile}`);
  console.log(`  improving: ${v.improving.length ? v.improving.join(", ") : "(none)"}`);
  console.log(`  regressing: ${v.regressing.length ? v.regressing.join(", ") : "(none)"}`);
  return v.verdict === "REGRESSION" ? 1 : 0;
}

process.exit(main());
