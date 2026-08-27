#!/usr/bin/env node
/**
 * evolution_triage.js — evolution-inbox 定时消费闭环（P0-B）
 *
 * 每周由 Windows 任务计划触发：
 *   1. 调用 evolution_scan.js 增量扫描会话日志；
 *   2. 聚合 inbox 中 status="new" 的条目，产出分诊报告
 *      （triage-latest.md + triage-YYYYMMDD.md 归档）；
 *   3. 把已纳入报告的条目从 "new" 改写为 "triaged"（记录 triagedAt），
 *      使 "new" 语义 = "上次分诊以来的新异常"；
 *   4. 报告含与上一期的同口径对比，供度量闭环。
 *
 * 用法: node scripts/evolution_triage.js [--dry-run]
 */
"use strict";
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const dryRun = process.argv.includes("--dry-run");
const repoRoot = path.resolve(__dirname, "..");
const scanScript = path.join(__dirname, "evolution_scan.js");
const inboxDir = path.join(
  process.env.USERPROFILE || process.env.HOME,
  ".agent-broker", "topics", "skills", "evolution-inbox", "workspace"
);
const inboxPath = path.join(inboxDir, "inbox.jsonl");
const latestPath = path.join(inboxDir, "triage-latest.md");

const HIGH_ALERT_THRESHOLD = 10; // 本期高危条目 ≥N 时报告头标 ⚠️

function readJsonl(p) {
  if (!fs.existsSync(p)) return [];
  let bad = 0;
  const out = fs.readFileSync(p, "utf8").split("\n").filter(Boolean).map((l) => {
    try { return JSON.parse(l); } catch { bad++; return null; }
  }).filter(Boolean);
  if (bad) console.warn(`evolution_triage: WARNING ${bad} unparseable line(s) in ${path.basename(p)} (kept as-is on rewrite)`);
  return out;
}

// 改写 inbox 时把无法解析的原始行原样保留，避免静默丢数据
function readRawLines(p) {
  if (!fs.existsSync(p)) return [];
  return fs.readFileSync(p, "utf8").split("\n").filter(Boolean);
}

function aggregate(entries) {
  // 同一会话可能有多个快照条目（会话增长后扫描会追加新条目），按 sessionId 去重保留最新
  const bySession = new Map();
  for (const e of entries) bySession.set(e.sessionId || e.createdAt, e);
  const deduped = [...bySession.values()];
  const byPattern = {};
  const totals = { inputTokens: 0, retries: 0, poll: 0, compactions: 0 };
  let high = 0;
  const top = [];
  for (const e of deduped) {
    const m = e.metrics || {};
    totals.inputTokens += m.inputTokens || 0;
    totals.retries += m.retries || 0;
    totals.poll += m.pollCount || 0;
    totals.compactions += m.compactions || 0;
    for (const a of e.anomalies || []) {
      const k = `${a.pattern}(${a.severity})`;
      byPattern[k] = (byPattern[k] || 0) + 1;
      if (a.severity === "high") high++;
    }
    top.push({ id: e.sessionId, title: (e.title || "").slice(0, 40), input: m.inputTokens || 0, retries: m.retries || 0, poll: m.pollCount || 0 });
  }
  top.sort((a, b) => b.input - a.input);
  return { byPattern, totals, high, top: top.slice(0, 5) };
}

function fmtInt(n) { return n.toLocaleString("en-US"); }

function buildReport(now, batch, prevSummary) {
  const { byPattern, totals, high, top } = aggregate(batch);
  const alert = high >= HIGH_ALERT_THRESHOLD ? "⚠️ " : "";
  const dateStr = now.toISOString().slice(0, 10);
  const lines = [];
  lines.push(`# ${alert}Evolution Inbox 分诊报告 ${dateStr}`);
  lines.push("");
  lines.push(`- 本期新条目（status=new）: **${batch.length}**`);
  lines.push(`- 本期高危: **${high}**（告警阈值 ${HIGH_ALERT_THRESHOLD}）`);
  lines.push(`- inputTokens 合计: ${fmtInt(totals.inputTokens)} | retries: ${fmtInt(totals.retries)} | poll: ${fmtInt(totals.poll)} | compactions: ${totals.compactions}`);
  if (prevSummary) {
    lines.push(`- 对比上期(${prevSummary.date}): 高危 ${prevSummary.high} → ${high}，retries ${fmtInt(prevSummary.retries)} → ${fmtInt(totals.retries)}，input ${fmtInt(prevSummary.inputTokens)} → ${fmtInt(totals.inputTokens)}`);
  }
  lines.push("");
  lines.push("## pattern × severity");
  lines.push("");
  for (const [k, v] of Object.entries(byPattern).sort((a, b) => b[1] - a[1])) lines.push(`- ${k}: ${v}`);
  lines.push("");
  lines.push("## TOP 5 token 热点");
  lines.push("");
  for (const t of top) lines.push(`- ${t.id} in=${fmtInt(t.input)} retries=${t.retries} poll=${t.poll} ${t.title}`);
  lines.push("");
  lines.push("## 处置建议");
  lines.push("");
  lines.push("- 高频 pattern 需要根治时：按 `evolution-proposal` 流程产提案（不要逐条处理存量）。");
  lines.push(`- 高危 ≥${HIGH_ALERT_THRESHOLD} 时已标 ⚠️，优先排查本期 TOP 热点会话。`);
  lines.push("");
  return { md: lines.join("\n"), summary: { date: dateStr, high, retries: totals.retries, inputTokens: totals.inputTokens, count: batch.length } };
}

function readPrevSummary() {
  try {
    const md = fs.readFileSync(latestPath, "utf8");
    const m = md.match(/<!--\s*triage-summary:(.*?)\s*-->/);
    return m ? JSON.parse(m[1]) : null;
  } catch { return null; }
}

function main() {
  // 1. 增量扫描
  const scanOut = execFileSync(process.execPath, [scanScript], { cwd: repoRoot, encoding: "utf8" });
  console.log(scanOut.trim().split("\n")[0]);

  // 2. 聚合条目（--rebuild：用全部条目重建基线报告，不改写状态）
  const rebuildAll = process.argv.includes("--rebuild");
  const entries = readJsonl(inboxPath);
  const batch = rebuildAll ? entries : entries.filter((e) => e.status === "new");
  if (!batch.length) { console.log("evolution_triage: no new entries, nothing to do"); return 0; }

  const now = new Date();
  const prev = readPrevSummary();
  const { md, summary } = buildReport(now, batch, prev);
  const report = md + `\n<!-- triage-summary:${JSON.stringify(summary)} -->\n`;

  if (dryRun) {
    console.log(`(dry-run) would triage ${batch.length} entries; report head:`);
    console.log(md.split("\n").slice(0, 8).join("\n"));
    return 0;
  }

  // 3. 落盘报告（latest + 归档）
  fs.mkdirSync(inboxDir, { recursive: true });
  fs.writeFileSync(latestPath, report, "utf8");
  fs.writeFileSync(path.join(inboxDir, `triage-${summary.date}.md`), report, "utf8");

  // 4. 原子改写 inbox：本批 new → triaged（--rebuild 模式跳过）；先备份，坏行原样保留
  if (!rebuildAll) {
    const triagedAt = now.toISOString();
    const batchIds = new Set(batch.map((e) => `${e.sessionId}|${e.createdAt}`));
    fs.copyFileSync(inboxPath, inboxPath + ".bak");
    const outLines = [];
    for (const raw of readRawLines(inboxPath)) {
      let e = null;
      try { e = JSON.parse(raw); } catch { outLines.push(raw); continue; } // 坏行原样保留
      if (e && batchIds.has(`${e.sessionId}|${e.createdAt}`)) {
        outLines.push(JSON.stringify({ ...e, status: "triaged", triagedAt }));
      } else {
        outLines.push(raw);
      }
    }
    const tmp = inboxPath + ".tmp";
    fs.writeFileSync(tmp, outLines.join("\n") + "\n", "utf8");
    fs.renameSync(tmp, inboxPath);
  }

  console.log(`evolution_triage: ${rebuildAll ? "rebuilt report over" : "triaged"} ${batch.length} entries (high=${summary.high}), report -> ${latestPath}`);
  return 0;
}

process.exit(main());
