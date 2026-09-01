#!/usr/bin/env node
// verify-deployment.mjs — workflow-model-preflight-gate 部署/升级后静态校验。
//
// 用途：DSH 升级或 profile rebuild 之后、live smoke test 之前运行：
//   node verify-deployment.mjs [--profile <dir>]
//
// 校验项（全部静态/离线，不触碰运行中的 DSH）：
//   1. 插件文件已部署到 profile plugins/ 且与仓库副本一致（SHA-256）；
//   2. cordis.patch.yml 含登记条目；
//   3. 插件能以真实 ESM 加载（node: 内置依赖可用、导出 name/inject/apply）；
//   4. 用 mock ctx 对真实 settings.yaml 跑一次判定：luna→拒绝(引导)、
//      未知模型→拒绝(fail closed)、已准入→放行、非 workflow→放行。
//
// 退出码：0 = 全部通过；1 = 任一失败。失败项会印在 stdout。

import { createHash } from 'node:crypto';
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const profileIdx = process.argv.indexOf('--profile');
const profileDir = profileIdx >= 0 ? process.argv[profileIdx + 1] : join(homedir(), '.dsh', 'profiles', 'web');

let failures = 0;
const check = (label, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}: ${label}${detail ? ' — ' + detail : ''}`);
  if (!ok) failures++;
};

const sha256 = (p) => createHash('sha256').update(readFileSync(p)).digest('hex');

// 1. 部署一致性
const deployed = join(profileDir, 'plugins', 'workflow-model-preflight-gate.mjs');
check('plugin file deployed', existsSync(deployed), deployed);
if (existsSync(deployed)) {
  check('deployed == repo copy (sha256)', sha256(deployed) === sha256(join(here, 'workflow-model-preflight-gate.mjs')));
}

// 2. patch 登记
const patchPath = join(profileDir, 'cordis.patch.yml');
const patchText = existsSync(patchPath) ? readFileSync(patchPath, 'utf8') : '';
check('cordis.patch.yml registration', /workflow-model-preflight-gate/.test(patchText) && /plugins\/workflow-model-preflight-gate\.mjs/.test(patchText), patchPath);

// 3+4. 模块加载与判定逻辑（对真实 settings.yaml）
if (existsSync(deployed)) {
  const mod = await import(pathToFileURL(deployed).href);
  check('exports name/inject/apply', mod.name === 'workflow-model-preflight-gate'
    && Array.isArray(mod.inject) && mod.inject.includes('tools')
    && typeof mod.apply === 'function');

  let guardFn = null;
  const warnings = [];
  mod.apply({ tools: { guard: (fn) => { guardFn = fn; } }, logger: { warn: (m) => warnings.push(m) } },
    { auditPath: join(here, '.verify-audit.tmp.jsonl') });
  check('guard registered', typeof guardFn === 'function');

  const mk = (name, args) => ({ name, arguments: args, agent: { options: { provider: 'kimi-coding' } } });
  const d1 = guardFn(mk('workflow', { script: "agent('x', { provider: 'cpa', model: 'gpt-5.6-luna' })", meta: { name: 't', description: 't' } }));
  check('unadmitted+policy -> deny with guidance', typeof d1 === 'string' && d1.includes('GATEWAY_ADMITTED_ALIAS_UPGRADE') && d1.includes('gpt-5.6-luna-max'));
  const d2 = guardFn(mk('workflow', { script: "agent('x', { provider: 'cpa', model: 'gpt-9.9-hyper-nonexistent' })", meta: { name: 't', description: 't' } }));
  check('unadmitted+no policy -> fail closed', typeof d2 === 'string' && d2.includes('UNADMITTED_OR_UNKNOWN_MODEL'));
  const d3 = guardFn(mk('workflow', { script: "agent('x', { provider: 'cpa', model: 'gpt-5.6-luna-max' })", meta: { name: 't', description: 't' } }));
  check('admitted -> pass', d3 === undefined);
  const d4 = guardFn(mk('pwsh', { command: 'echo hi' }));
  check('non-workflow tool -> pass', d4 === undefined);
  check('no internal warnings', warnings.length === 0, warnings.join('; '));
}

console.log(failures === 0 ? 'VERIFY_DEPLOYMENT: PASS' : `VERIFY_DEPLOYMENT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
