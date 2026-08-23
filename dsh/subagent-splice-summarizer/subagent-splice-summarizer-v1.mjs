// User-level Cordis plugin: subagent-splice-summarizer
//
// Listens on the `agent/pre-step` waterfall and, for messages whose source is
// `subagent-report` and whose text exceeds `thresholdChars` (default 8000),
// lossily summarizes text while preserving all non-text blocks and keeping
// governance fields (status / changed / validation / deviations / blocker /
// artifact / contract / goal / scope / must not / exit / next) plus the first
// few lines of each section. Non-subagent-report messages and short reports
// pass through untouched.
//
// Goal (structural cache protection): a subagent's long report is no longer
// spliced verbatim into the parent context. This is a volume protection layer,
// not a cache silver bullet — the dominant cost in audited sessions was prefix
// re-computation across many injections, not the report text itself. See the
// A/B experiment script in the governance skill for numbers.
//
// Why a user-level plugin: persists across sessions and process restarts,
// protecting every session in the profile from oversized spliced reports.
//
// Deploy: place this file in ~/.dsh/profiles/<profile>/plugins/ and register
// it in the profile's cordis.patch.yml (see cordis.patch.yml in this package).

export const name = 'subagent-splice-summarizer';

const DEFAULT_THRESHOLD_CHARS = 8000;

/** Extract a structured summary: keep tagged sections' headers + first lines. */
function buildSummary(raw) {
  const lines = raw.split('\n');
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
          if (tagPattern.test(next) || next.trim() === '') break;
          kept.push(next);
        }
        kept.push('');
      }
    }
  }
  if (kept.length < 5) {
    const maxLen = 4000;
    if (raw.length <= maxLen) return raw;
    return raw.slice(0, 2000) + '\n\n...[中间 ' + (raw.length - 4000) + ' 字符已截断]...\n\n' + raw.slice(-2000);
  }
  const result = '[摘要] 子代理报告结构摘要：\n' + kept.join('\n').trim();
  if (result.length > 6000) {
    return result.slice(0, 3000) + '\n\n...[长摘要已截断]...\n\n' + result.slice(-2000);
  }
  return result;
}

function extractText(blocks) {
  if (!blocks) return '';
  return blocks.filter((b) => b.type === 'text').map((b) => b.text || '').join('');
}

export function apply(ctx, config = {}) {
  const configuredThreshold = Number(config.thresholdChars);
  const thresholdChars = Number.isFinite(configuredThreshold) && configuredThreshold > 0
    ? configuredThreshold
    : DEFAULT_THRESHOLD_CHARS;

  ctx.on('agent/pre-step', async (payload, next) => {
    const decision = await next();
    if (decision.kind !== 'enter') return decision;

    const newMessages = decision.messages.map((msg) => {
      if (msg.role === 'user' && msg.source && msg.source.kind === 'subagent-report') {
        const text = extractText(msg.content);
        if (text.length <= thresholdChars) return msg;
        const summary = buildSummary(text);
        let insertedSummary = false;
        const content = [];
        for (const block of msg.content) {
          if (block.type === 'text') {
            if (!insertedSummary) {
              content.push({ type: 'text', text: summary });
              insertedSummary = true;
            }
          } else {
            content.push(block);
          }
        }
        return { ...msg, content };
      }
      return msg;
    });

    return { kind: 'enter', messages: newMessages };
  });
}