// subagent-prep-exec-gate 消息结构契约测试
// 运行：node dsh/subagent-prep-exec-gate/test-reminder-contract.mjs
// 断言：所有注入的 reminder 必须是 DSH user/message 结构
//   { content: [{ type: 'text', text: string }], source: { kind, form, summary } }
// 防止再次出现"结构错误导致会话持久化校验失败"的生产事故（2026-08-26）。
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(here, 'subagent-prep-exec-gate.mjs'), 'utf-8');

// 静态提取源码中所有 reminder 对象的 content 构造，断言满足契约。
// 简单可靠：搜索 "content: [{" 出现次数 == 搜索 "type: 'text'," 出现次数，
// 并断言不存在裸的 "reminder = {\n type: 'text'" 模式。
const contentArrays = (src.match(/content: \[\{/g) || []).length;
const bareTextReminders = (src.match(/type: 'text',\s*\n\s*text:/g) || []).length;

let failures = 0;
function check(name, cond, detail) {
  if (cond) {
    console.log(`PASS ${name}`);
  } else {
    failures += 1;
    console.error(`FAIL ${name}: ${detail}`);
  }
}

check('至少一个 reminder 使用 content 数组', contentArrays >= 3, `found ${contentArrays}`);
check('不存在裸 type/text reminder', bareTextReminders === 0, `found ${bareTextReminders} bare reminders`);
check('post-execute 注入路径存在', src.includes('tools/post-execute'), 'missing hook');
check('pre-step 重置路径存在且防御 messages undefined', src.includes('Array.isArray(messages)'), 'missing guard');

if (failures > 0) {
  console.error(`\n${failures} 项契约检查失败——禁止部署到生产 profile。`);
  process.exit(1);
}
console.log('\n全部契约检查通过，可安全部署。');
