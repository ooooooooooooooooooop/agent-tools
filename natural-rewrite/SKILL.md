---
name: natural-rewrite
description: Rewrite messages, emails, comments, explanations, social posts, or copy into natural human-sounding wording while preserving meaning. Use for Chinese or English text polishing, de-AI wording, WeChat replies, private messages, email drafts, comments, and concise natural rewrites; do not add unconfirmed facts.
---

# Natural Rewrite

Use this skill to make text sound natural, concise, and context-appropriate without changing the facts.

## Rules

- Preserve the original meaning.
- Do not add unconfirmed information.
- Do not over-polish or expand without being asked.
- Avoid template tone, translation tone, and excessive politeness.
- Default to Chinese unless the input or user request indicates another language.
- Match the relationship, channel, and stakes of the message.
- If the user asks for one version, output only one version.
- If the user does not specify a count, provide one to three versions.

## Preservation Pass

Before polishing, identify and preserve named entities, numbers, dates, URLs, negation, commitments, attribution, uncertainty, and requested formatting. Keep the original level of certainty: do not turn “可能” into a fact, a question into a promise, or a personal opinion into an organizational position.

If the source is ambiguous, preserve the ambiguity or flag it briefly; never resolve it by inventing context. For sensitive, legal, financial, medical, or interpersonal content, optimize wording only and do not broaden the claim or add advice unless requested.

## Default Versions

- Direct: concise and clear.
- Warm: slightly softer and more considerate.
- Relaxed: more casual when the context allows it.

## Avoid

Avoid phrases such as:
- "希望这封邮件找到你安好"
- "诚挚地"
- "非常荣幸"
- "期待您的回复"

Use them only when the context is genuinely formal and the user wants that tone.

## Output

When multiple versions are useful:

```text
直接版：

温和版：

轻松版：
```

After rewriting, silently check that the meaning-bearing facts and constraints above are still present. If the user asks for a single version, do not expose this checklist or add alternatives.
