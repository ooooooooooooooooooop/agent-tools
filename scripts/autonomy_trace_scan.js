#!/usr/bin/env node
/**
 * autonomy_trace_scan.js — 自主执行失败轨迹扫描器（AUTONOMOUS_INTENT_TO_COMPLETION 交付物）
 *
 * 扫描 ~/.dsh/sessions 全部会话日志（多帧 zstd JSONL），做两件事：
 *   1) hits：定位"用户负反馈"消息（继续/为什么停了/不要问我/偏题了/……），
 *      抽取其前后执行上下文（前一条 assistant 文本、前 N 个工具调用、后续用户反应），
 *      供失败轨迹清单与 shadow replay 使用；
 *   2) metrics：每会话机械指标（steps/tokens/retry/compaction/重复调用游程/read:exec 比/
 *      验证类命令占比/用户消息数），供 runtime metrics 基线。
 *
 * 只读扫描，不修改任何会话数据。输出文件默认落在 ~/.dsh/.evolution-inbox/（本地运行态，不进发布集）。
 *
 * 用法:
 *   node scripts/autonomy_trace_scan.js [--force] [--sessions-root DIR] [--hits-out FILE] [--metrics-out FILE] [--state FILE]
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { zstdDecompressSync } = require("node:zlib");

const ZSTD_MAGIC = 4247762216;

// ---- zstd 多帧解码（与 scripts/evolution_scan.js 同一套帧扫描逻辑）----
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

// ---- 用户负反馈短语组（源自 AUTONOMOUS_INTENT_TO_COMPLETION 任务书第 1 节）----
// 只负责"命中定位 + 上下文抽取"，分类结论由后续人工/子代理读上下文判定。
const PATTERN_GROUPS = {
  UNNECESSARY_CLARIFICATION: [
    "不要问我", "别问我", "不用再问", "别再问", "自己判断", "自己查", "你自己定",
    "自己做主", "为什么又让我决定", "你不是可以自己做吗", "这也要问", "别什么都问",
  ],
  PREMATURE_STOP: [
    "为什么停了", "怎么停了", "为什么又停", "怎么就停了", "不能因为这个就停",
    "怎么又停了", "为什么停下来",
  ],
  CONTINUATION_PUSH: [
    "继续", "接着做", "做完", "自主推进", "按之前目标完成", "继续做", "继续下去",
  ],
  PREPARATION_LOOP: [
    "怎么还没开始做", "还没开始做", "你又在研究什么", "一直在研究", "别研究了",
    "研究够了吧", "怎么还在查",
  ],
  VERIFICATION_LOOP: [
    "怎么一直在测试", "一直在测试", "又在测试", "别测了", "测了这么多遍",
    "一直在验证", "怎么还在测",
  ],
  GOAL_DRIFT: [
    "偏题了", "答非所问", "跟目标有什么关系", "跑题了", "离题了", "这不是我要的",
  ],
  RECOVERY_HINT: [
    "网上查", "换办法", "换个方法", "换个思路", "换一种方式",
  ],
};

const MUTATION_TOOLS = new Set(["edit", "write"]);
const READ_TOOLS = new Set(["read", "grep", "glob"]);
const TEST_CMD_RE = /(unittest|pytest|npm test|pnpm test|validate_repo|quality_report|run_tests|validate\b)/i;

function excerpt(s, n) {
  if (!s) return "";
  const t = String(s).replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n) + "…" : t;
}

function findSessions(root) {
  const out = [];
  if (!fs.existsSync(root)) return out;
  for (const proj of fs.readdirSync(root)) {
    const projDir = path.join(root, proj);
    let st;
    try { st = fs.statSync(projDir); } catch { continue; }
    if (!st.isDirectory()) continue;
    for (const sd of fs.readdirSync(projDir)) {
      const logFile = path.join(projDir, sd, "session.jsonl.zstd");
      let fst;
      try { fst = fs.statSync(logFile); } catch { continue; }
      if (!fst.isFile()) continue;
      out.push({ logFile, project: proj, sessionId: sd, mtimeMs: fst.mtimeMs, size: fst.size });
    }
  }
  return out;
}

function readJsonl(file) {
  if (!fs.existsSync(file)) return [];
  let text;
  try { text = fs.readFileSync(file, "utf8"); } catch { return []; }
  const out = [];
  for (const line of text.split("\n")) {
    try {
      const value = JSON.parse(line);
      if (value && typeof value === "object") out.push(value);
    } catch {}
  }
  return out;
}

function sessionKey(project, sessionId) {
  return JSON.stringify([project || "", sessionId || ""]);
}

function scanSession(f, hitsOut) {
  let text;
  try { text = decompress(f.logFile); } catch { return null; }
  const events = [];
  for (const l of text.split("\n")) {
    let o;
    try { o = JSON.parse(l); } catch { continue; }
    events.push(o);
  }
  const m = {
    sessionId: f.sessionId, project: f.project, title: "",
    steps: 0, calls: 0, input: 0, output: 0, cache: 0,
    retries: 0, compactions: 0, reads: 0, mutations: 0, greps: 0,
    testCmds: 0, pwshCmds: 0, userMsgs: 0, subagents: 0, forks: 0,
    polls: 0, longPolls: 0, maxIdenticalRun: 1, askToolCalls: 0,
    isSubagent: false, hitCount: 0, hitGroups: {},
    createdAt: null, lastActivity: null,
  };
  const POLL = new Set(["request_status", "job_list", "list_agents", "get_goal", "get_topic_status", "get_cli_requests", "get_codex_requests", "get_claude_requests", "get_antigravity_requests", "get_consultation_history", "list_managed_claude_supervisors", "get_topic_timeline", "get_request_ledger", "get_managed_claude_supervisor", "get_work_memory", "get_latest_context_snapshot"]);
  const LONGPOLL = new Set(["wait_supervisor_event", "request_result", "job_output", "wait_task_receipt"]);

  // 线性事件序列（只保留分析需要的轻量视图）
  const seq = []; // {kind:'user'|'asstText'|'tool'|'other', ...}
  let firstUserSeen = false;
  for (const o of events) {
    if (typeof o.time === "number") {
      if (!m.lastActivity || o.time > m.lastActivity) m.lastActivity = o.time;
    }
    switch (o.type) {
      case "session": m.createdAt = o.createdAt || null; break;
      case "subagent/descriptor": m.isSubagent = true; break;
      case "session/title":
        if (!m.title && o.data && (o.data.title || o.data.name)) m.title = excerpt(o.data.title || o.data.name, 80);
        break;
      case "step/start": m.steps++; break;
      case "llm/retry": m.retries++; break;
      case "compaction/start": m.compactions++; break;
      case "user/message": {
        const d = o.data || {};
        const kind = d.source && d.source.kind;
        if (kind !== "user") break; // 只统计真实用户输入，排除注入/系统消息
        const txt = (d.content || []).filter((c) => c.type === "text").map((c) => c.text || "").join("\n");
        m.userMsgs++;
        seq.push({ kind: "user", text: txt, time: o.time, seqNo: o.seq, isFirst: !firstUserSeen });
        firstUserSeen = true;
        break;
      }
      case "assistant/message": {
        const usage = o.data && o.data.usage;
        if (usage && typeof usage.inputTokens === "number") {
          m.calls++;
          m.input += usage.inputTokens || 0;
          m.output += usage.outputTokens || 0;
          m.cache += usage.cacheReadTokens || 0;
        }
        const blocks = (o.data && o.data.message && o.data.message.content) || [];
        const txt = blocks.filter((b) => b.type === "text").map((b) => b.text || "").join("\n");
        const toolBlocks = blocks.filter((b) => b.type === "tool-call");
        if (txt.trim()) seq.push({ kind: "asstText", text: txt, time: o.time });
        for (const tb of toolBlocks) {
          if (tb.name === "ask_user_question") m.askToolCalls++;
        }
        break;
      }
      case "tool/call": {
        const name = (o.data && (o.data.name || (o.data.tool && o.data.tool.name))) || "?";
        let args = "";
        try { args = o.data.arguments || ""; } catch {}
        if (name === "read") m.reads++;
        if (name === "grep" || name === "glob") m.greps++;
        if (MUTATION_TOOLS.has(name)) m.mutations++;
        if (POLL.has(name)) m.polls++;
        if (LONGPOLL.has(name)) m.longPolls++;
        if (name === "subagent") m.subagents++;
        if (name === "subagent_fork") m.forks++;
        if (name === "pwsh") {
          m.pwshCmds++;
          if (TEST_CMD_RE.test(args)) m.testCmds++;
        }
        // 相同调用（同工具同参数）连续游程 —— NO_PROGRESS 机械代理信号
        const sig = name + "|" + args.slice(0, 200);
        if (seq.length && seq[seq.length - 1].kind === "tool" && seq[seq.length - 1].sig === sig) {
          seq[seq.length - 1].run = (seq[seq.length - 1].run || 1) + 1;
          if (seq[seq.length - 1].run > m.maxIdenticalRun) m.maxIdenticalRun = seq[seq.length - 1].run;
        } else {
          seq.push({ kind: "tool", name, args: excerpt(args, 80), sig, run: 1, time: o.time });
        }
        break;
      }
    }
  }

  // ---- 负反馈命中 + 上下文抽取 ----
  let hitIdx = 0;
  for (let i = 0; i < seq.length; i++) {
    const e = seq[i];
    if (e.kind !== "user" || e.isFirst) continue; // 首条用户消息是任务下达，不是反馈
    const t = e.text;
    if (!t) continue;
    const matchedGroups = [];
    const matchedPhrases = [];
    for (const [group, phrases] of Object.entries(PATTERN_GROUPS)) {
      for (const p of phrases) {
        if (t.includes(p)) {
          matchedGroups.push(group);
          matchedPhrases.push(p);
          break; // 每组只记一次
        }
      }
    }
    if (!matchedGroups.length) continue;
    // 前一条 assistant 文本
    let prevAsst = "";
    for (let j = i - 1; j >= 0; j--) {
      if (seq[j].kind === "asstText") { prevAsst = seq[j].text; break; }
      if (seq[j].kind === "user") break;
    }
    // 前 8 个工具调用
    const prevTools = [];
    for (let j = i - 1; j >= 0 && prevTools.length < 8; j--) {
      if (seq[j].kind === "tool") prevTools.unshift(seq[j].name + (seq[j].run > 1 ? "x" + seq[j].run : ""));
      if (seq[j].kind === "user") break;
    }
    // 前一条 assistant 是否带提问特征
    const prevAsked = /[?？]/.test(prevAsst) || /(是否|要不要|能否|可以吗|请选择|哪个)/.test(prevAsst);
    // 后续用户反应（下一条用户消息）
    let nextUser = "";
    for (let j = i + 1; j < seq.length; j++) {
      if (seq[j].kind === "user") { nextUser = seq[j].text; break; }
    }
    hitIdx++;
    m.hitCount++;
    for (const g of matchedGroups) m.hitGroups[g] = (m.hitGroups[g] || 0) + 1;
    hitsOut.push({
      schema: "autonomy-feedback-hit/v1",
      sessionId: f.sessionId,
      project: f.project,
      title: m.title,
      isSubagentSession: m.isSubagent,
      hitIdx,
      time: e.time ? new Date(e.time).toISOString() : null,
      groups: matchedGroups,
      phrases: matchedPhrases,
      userText: excerpt(t, 260),
      prevAssistant: excerpt(prevAsst, 320),
      prevAssistantAsked: prevAsked,
      prevToolCalls: prevTools,
      nextUserReaction: excerpt(nextUser, 200),
      sessionSteps: m.steps,
    });
  }
  return m;
}

function main() {
  const args = process.argv.slice(2);
  const opts = {
    sessionsRoot: path.join(os.homedir(), ".dsh", "sessions"),
    hitsOut: path.join(os.homedir(), ".dsh", ".evolution-inbox", "autonomy-feedback-hits.jsonl"),
    metricsOut: path.join(os.homedir(), ".dsh", ".evolution-inbox", "autonomy-session-metrics.jsonl"),
    state: null,
    force: false,
  };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--sessions-root") opts.sessionsRoot = args[++i];
    else if (args[i] === "--hits-out") opts.hitsOut = args[++i];
    else if (args[i] === "--metrics-out") opts.metricsOut = args[++i];
    else if (args[i] === "--state") opts.state = args[++i];
    else if (args[i] === "--force") opts.force = true;
    else if (args[i] === "--help") {
      console.log("usage: node scripts/autonomy_trace_scan.js [--force] [--sessions-root DIR] [--hits-out FILE] [--metrics-out FILE] [--state FILE]");
      process.exit(0);
    }
  }
  if (!opts.state) opts.state = path.join(path.dirname(opts.metricsOut), "scan-state.json");

  const files = findSessions(opts.sessionsRoot);
  let state = { version: 1, seen: {} };
  let hasState = false;
  try {
    const saved = JSON.parse(fs.readFileSync(opts.state, "utf8"));
    if (saved && saved.seen && typeof saved.seen === "object") {
      state = saved;
      hasState = true;
    }
  } catch {}
  const oldOutputsPresent = fs.existsSync(opts.hitsOut) && fs.existsSync(opts.metricsOut);

  const hitsBySession = new Map();
  for (const h of readJsonl(opts.hitsOut)) {
    if (h.sessionId) {
      const key = sessionKey(h.project, h.sessionId);
      if (!hitsBySession.has(key)) hitsBySession.set(key, []);
      hitsBySession.get(key).push(h);
    }
  }
  const metricsBySession = new Map();
  for (const m of readJsonl(opts.metricsOut)) {
    if (m.sessionId) metricsBySession.set(sessionKey(m.project, m.sessionId), m);
  }

  let scanned = 0;
  let skipped = 0;
  let done = 0;
  for (const f of files) {
    const key = sessionKey(f.project, f.sessionId);
    const prev = state.seen[f.logFile];
    if (!opts.force && hasState && oldOutputsPresent && prev && prev.mtimeMs === f.mtimeMs && prev.size === f.size) {
      skipped++;
      continue;
    }
    const sessionHits = [];
    const m = scanSession(f, sessionHits);
    scanned++;
    if (m) {
      metricsBySession.set(key, m);
      hitsBySession.set(key, sessionHits);
      state.seen[f.logFile] = { mtimeMs: f.mtimeMs, size: f.size };
    }
    else {
      metricsBySession.delete(key);
      hitsBySession.delete(key);
    }
    done++;
    if (done % 100 === 0) console.error(`  …scanned ${done}/${files.length}`);
  }

  const hits = [...hitsBySession.values()].flat();
  const metrics = [...metricsBySession.values()];
  fs.mkdirSync(path.dirname(opts.hitsOut), { recursive: true });
  fs.mkdirSync(path.dirname(opts.metricsOut), { recursive: true });
  fs.mkdirSync(path.dirname(opts.state), { recursive: true });
  fs.writeFileSync(opts.hitsOut, hits.map((h) => JSON.stringify(h)).join("\n") + (hits.length ? "\n" : ""), "utf8");
  fs.writeFileSync(opts.metricsOut, metrics.map((x) => JSON.stringify(x)).join("\n") + (metrics.length ? "\n" : ""), "utf8");
  fs.writeFileSync(opts.state, JSON.stringify(state, null, 2), "utf8");
  // 摘要
  const byGroup = {};
  for (const h of hits) for (const g of h.groups) byGroup[g] = (byGroup[g] || 0) + 1;
  console.log(`autonomy_trace_scan: ${files.length} sessions, ${metrics.length} parsed, ${hits.length} feedback hits`);
  console.log(`scanned ${scanned}, skipped ${skipped} unchanged`);
  for (const [g, c] of Object.entries(byGroup).sort((a, b) => b[1] - a[1])) console.log(`  ${g}: ${c}`);
  const withHits = new Set(hits.map((h) => h.sessionId)).size;
  console.log(`  sessions with hits: ${withHits}`);
  console.log(`  hits -> ${opts.hitsOut}`);
  console.log(`  metrics -> ${opts.metricsOut}`);
}

process.exit(main() || 0);
