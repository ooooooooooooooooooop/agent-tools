// dsh-world-model：Personal AI 世界模型机制层插件（身体侧适配器）
// theory: V0.3.1 | schema: 1.1
//
// 身份：插件是 Personal AI 身体上的器官（反射弧/海马体），不是世界模型本身。
//   灵魂 = canonicalDir 下的 W/V/U/L 状态（harness 无关，可随身体迁移）。
//   身体 = DSH；本插件只认 canonicalDir/stateDir/bodyId 三个接口。
//
// 机制（非提示词）：
//   - world_model 工具：activate/model/predict/observe/evaluate/update/probe/
//     meta/value/input/persist/status/declassify → 真实落盘
//   - tools/result → RAW_EVIDENCE（L0）机械捕获，带 source/channel/tool provenance
//   - tools.guard → CORE/FULL：consequential mutation 无绑定预测不放行
//   - session 首事件 → 结构化 briefing（canonical 编译产物）注入 + STATE_RESTORE
//   - INPUT_SEMANTICS 路由：EPISTEMIC_CLAIM→W / NORMATIVE_DIRECTIVE→查 U 权威 /
//     AUTHORIZATION→许可记录 / DURABLE_VALUE→value proposal / PREFERENCE→局部
//   - body lease：同一 entity_id 只允许一个 canonical writer（runtime-state.json）
//   - fork 检测：canonical lineage_head 与本 body 记录不一致 → FORK_DETECTED
//   - L1 ledger/runs append-only；canonical 只写 proposals/（治理路径）
//
// 用法：plugins/ + cordis.patch.yml 条目。模式 config.mode/env DSH_WM_MODE。

import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { homedir, hostname } from 'node:os';
import { randomUUID, createHash } from 'node:crypto';

export const name = 'dsh-world-model';
export const inject = ['tools', 'systemPrompt'];

const SCHEMA_VERSION = '1.2';
const THEORY_VERSION = '0.4';
const BCC_VERSION = 'BCC-1';
const BRIEF_HARD_CAP = 8 * 1024;
// 工具名归一：小写 + 去 -_ 分隔 + 去可执行后缀（str-replace-editor /
// StrReplaceEditor / str_replace_editor / sh.exe 归一到同一身份）——
// 分隔符变体和大小写不再是逃逸面。
function normToolName(n) {
  // 先去可执行后缀（.exe/.bat…），再把 -_ . / : 空白全切掉——
  // fs.write / fs_write / fs:write / FS.WRITE 归一到同一身份
  return String(n).toLowerCase().trim().replace(/\.(exe|com|bat|cmd|ps1)$/i, '').replace(/[-_\s./:]/g, '');
}
const CONSEQUENT_TOOLS = new Set(['edit', 'write', 'str-replace-editor', 'str_replace_editor', 'notebook_edit', 'exec', 'mcp_call_tool', 'apply_patch', 'write_to_process', 'request_scope',
  // 跨 harness 常见别名——守卫集宁宽勿窄（不在 harness 里的名字无代价，
  // 在而没登记的 = 完全无守卫的变更通道）。
  'bash', 'shell', 'terminal', 'sh', 'powershell', 'pwsh', 'cmd', 'run', 'command',
  'run_command', 'run-command', 'execute', 'execute_command', 'execute-command',
  'run_terminal_command', 'shell_exec', 'editor', 'file_editor', 'file-editor',
  'str_replace', 'patch', 'create_file', 'delete_file', 'move_file', 'fs_write',
  'fs_edit', 'browser', 'computer', 'computer_use', 'mcp', 'call_tool', 'tool_call',
  'multi_edit', 'multiedit', 'multi-edit', 'call_mcp_tool', 'write_file', 'edit_file',
  'replace_in_file', 'rename_file', 'copy_file'].map(normToolName));
// 词根前缀：归一名以这些开头 = 命令执行/文件删除移动/GUI 动作类
const CONSEQUENT_STEMS = ['exec', 'shell', 'bash', 'terminal', 'powershell', 'pwsh',
  'shellexec', 'runcommand', 'runterminal', 'run', 'mcp', 'calltool', 'toolcall', 'callmcp',
  'browser', 'computer', 'deletefile', 'movefile', 'createfile', 'writefile',
  'editfile', 'replaceinfile', 'renamefile', 'copyfile', 'patchfile', 'applypatch',
  'notebookedit', 'notebookwrite', 'strreplace', 'multiedit', 'editor',
  'texteditor', 'fs', 'os', 'sys', 'node', 'io', 'delete', 'move', 'rename',
  'kill'];
// 只读白名单：归一名以这些开头 = 纯读/元操作，不需要预测仪式。
// 其余一切未知工具名一律 consequential（denylist 枚举不完 = 反转默认，
// 新工具名最坏结果是多要一次 predict——安全方向）。
// 注意顺序：consequential 判定先于白名单——notebookedit 命中 notebook* 词根，
// notebookread 才落到这里。不收录 printenv/whoami/echo（信息泄漏面）。
const SAFE_READONLY_STEMS = ['read', 'glob', 'grep', 'search', 'find', 'list',
  'fetch', 'get', 'view', 'status', 'show', 'describe', 'cat', 'head',
  'tail', 'ls', 'dir', 'websearch', 'webfetch', 'codesearch', 'askuser',
  'worldmodel', 'dshworldmodel', 'skill', 'todowrite', 'notebookread',
  'resolve', 'exists', 'count', 'diff', 'inspect'];
function isConsequential(name) {
  const tn = normToolName(name);
  if (!tn) return true;   // 无名工具不可绑定预测 → 按 consequential 挡（fail closed）
  if (CONSEQUENT_TOOLS.has(tn) || CONSEQUENT_STEMS.some(st => tn.startsWith(st))) return true;
  return !SAFE_READONLY_STEMS.some(st => tn.startsWith(st));
}
// 默认不可逆：参数=任意命令/远端调用/权限申请/GUI动作/删除移动的通道，载荷不可
// 静态证安全 → 一律要求 irreversible:true 预测。扫描器在此之下只剩纵深意义。
// 文件编辑类（edit/write/patch/fs_write）保持检测层——VCS 下可回滚。
const IRREVERSIBLE_BY_DEFAULT = new Set(['exec', 'mcp_call_tool', 'write_to_process', 'request_scope',
  'bash', 'shell', 'terminal', 'sh', 'powershell', 'pwsh', 'cmd', 'run', 'command',
  'run_command', 'run-command', 'execute', 'execute_command', 'execute-command',
  'run_terminal_command', 'shell_exec', 'mcp', 'call_tool', 'tool_call',
  'delete_file', 'move_file', 'browser', 'computer', 'computer_use', 'multi_edit'].map(normToolName));
const IRREVERSIBLE_STEMS = ['exec', 'shell', 'bash', 'terminal', 'powershell', 'pwsh',
  'shellexec', 'runcommand', 'runterminal', 'mcp', 'calltool', 'toolcall', 'callmcp',
  'browser', 'computer', 'deletefile', 'movefile'];
function isIrreversibleByDefault(name) {
  const tn = normToolName(name);
  return IRREVERSIBLE_BY_DEFAULT.has(tn) || IRREVERSIBLE_STEMS.some(st => tn.startsWith(st));
}
// 不可逆判定（宽检测、安全方向偏向）：arguments 全部字符串值展平 → unicode 归一
// （全角/长短破折号→-，弯引号→'，驼峰边界断词）→ 按空白+shell 元字符+路径分隔
// 切词（管道/分号/$(…)/重定向/路径段都断词）→ 剥引号 → flag 归一 → basename/
// 版本号/.exe 归一 → 全位置签名扫描。
// 误报 = 仅要求 prediction 带 irreversible:true（安全方向）；漏报 = 漏洞。
function flattenStrings(v, acc) {
  if (v == null) return acc;
  const t = typeof v;
  if (t === 'string' || t === 'number' || t === 'boolean') acc.push(String(v));
  else if (Array.isArray(v)) for (const x of v) flattenStrings(x, acc);
  else if (t === 'object') for (const k of Object.keys(v)) flattenStrings(v[k], acc);
  return acc;
}
const CMD_WRAPPERS = new Set(['sudo', 'doas', 'env', 'nohup', 'nice', 'ionice', 'time', 'timeout', 'watch', 'xargs', 'parallel', 'command', 'exec', 'start', 'runas', 'busybox', 'sshpass', 'stdbuf', 'strace', 'ltrace', 'unbuffer', 'expect', 'ssh', 'wsl']);
// 内联代码解释器：-c/-e/-Command/-EncodedCommand/-m/-jar 等 → 载荷不可静态证安全 → 必标
const INTERPRETERS = new Set(['python', 'python3', 'py', 'node', 'nodejs', 'deno', 'bun', 'perl', 'ruby', 'php', 'lua', 'osascript', 'mshta', 'rundll32', 'regsvr32', 'installutil', 'wscript', 'cscript', 'wmic', 'bash', 'sh', 'zsh', 'fish', 'dash', 'ksh', 'powershell', 'pwsh', 'cmd', 'eval', 'source', 'iex', 'invoke-expression', 'groovy', 'jjs', 'irb', 'java', 'rscript', 'msbuild', 'dotnet', 'forfiles', 'nc', 'ncat', 'netcat', 'go']);
// 裸用即破坏的命令（删/覆写/擦除/分区/服务/进程终止）
const DESTRUCTIVE_CMDS = new Set(['rm', 'rmdir', 'del', 'erase', 'rd', 'ri', 'unlink', 'shred', 'srm', 'wipe', 'sdelete', 'wipefs', 'remove-item', 'clear-content', 'set-content', 'rimraf', 'format', 'dd', 'diskpart', 'sfdisk', 'shutdown', 'reboot', 'poweroff', 'halt', 'init', 'telinit', 'fdisk', 'parted', 'bcdedit', 'vssadmin', 'wevtutil', 'fsutil', 'chattr', 'tee', 'truncate', 'mv', 'robocopy', 'cipher', 'kill', 'pkill', 'taskkill', 'umount', 'swapoff', 'rmmod', 'modprobe', 'setenforce', 'takeown', 'icacls', 'attrib', 'passwd', 'userdel', 'groupdel', 'stop-service', 'remove-service', 'restart-computer']);
// host 工具 + 危险子命令矩阵
const HOST_TOOLS = new Set(['git', 'docker', 'podman', 'nerdctl', 'buildah', 'kubectl', 'terraform', 'npm', 'pip', 'pip3', 'yarn', 'pnpm', 'apt', 'apt-get', 'dnf', 'pacman', 'zypper', 'snap', 'flatpak', 'winget', 'choco', 'scoop', 'gem', 'cargo', 'composer', 'brew', 'helm', 'redis-cli', 'mongo', 'mongosh', 'mysql', 'psql', 'sqlite3', 'az', 'aws', 'gcloud', 'sc', 'schtasks', 'reg', 'dism', 'netsh', 'net', 'iptables', 'ufw', 'systemctl', 'find', 'sed', 'perl', 'awk', 'chmod', 'chown', 'mv', 'cp', 'copy', 'move', 'xcopy', 'svn', 'hg', 'p4', 'crontab', 'at', 'cmdkey', 'gpg', 'rsync', 'rclone', 'curl', 'wget', 'certutil', 'bitsadmin', 'scp', 'msiexec', 'tar', 'unzip', 'virsh', 'drush']);
const DANGER_SUBS = new Set(['delete', 'destroy', 'prune', 'uninstall', 'unpublish', 'publish', 'flushall', 'flushdb', 'flush', 'remove', 'purge', 'drop', 'truncate', 'reset', 'clean', 'cleanup', 'restore', 'expire', 'clear', 'disable', 'stop', 'kill', 'terminate', 'wipe', 'create', 'add', 'config', 'poweroff', 'reboot', 'shutdown', 'undefine', 'strip', 'autoremove', 'apply', 'drain', 'rmi', 'rb', 'erase',
  // 包安装/容器执行/远程拉取 = 任意代码执行面（scoped：仅当 host 词同现）
  'install', 'require', 'i', 'ci', 'run', 'exec', 'start', 'up', 'login', 'pull', 'load', 'import']);
// LOLBin：任意参数形态都可执行代码/拉取载荷——存在即标
const LOLBINS = new Set(['mshta', 'regsvr32', 'rundll32', 'msiexec', 'javaws', 'wscript', 'cscript', 'hh', 'installutil', 'regasm', 'regsvcs', 'pcalua', 'url.dll', 'msbuild', 'dnx', 'rcsi', 'csi']);
const SCRIPT_EXTS = new Set(['py', 'js', 'mjs', 'cjs', 'sh', 'ps1', 'bat', 'cmd', 'rb', 'pl', 'php', 'lua', 'vbs', 'vbe', 'hta', 'jar', 'exe', 'dll', 'wsf', 'reg', 'inf', 'txt']);
// basename/版本/后缀归一：python3.11 / python.exe / /usr/bin/python3 → python
function normWord(w) { return w.replace(/\.(exe|com|dll)$/i, '').replace(/[\d.]+$/, ''); }
function isIrreversibleArgs(args) {
  const raw = flattenStrings(args, []);
  if (!raw.length) return false;
  const norm = s => s.replace(/[‐‑‒–—―−﹘﹣－]/g, '-').replace(/[‘’‚‛]/g, "'").replace(/[“”„‟]/g, '"').replace(/([a-z])(?=[A-Z])/g, '$1 ');
  const rawJoined = raw.map(norm).join(' ').toLowerCase();
  const toks = raw
    .map(norm)
    // bash 转义拼接 'r\m'→'rm'：剥掉 \ 转义符再切词，否则 rm 永不形成
    .map(s => s.replace(/\\(.)/g, '$1'))
    .flatMap(s => s.split(/(\s+|[;&|(){}<>`$\/\\.,=:_]|\n)/))
    // 全量剥引号（不只两端）：'r''m' → rm
    .map(s => s.replace(/["'`]/g, '').toLowerCase())
    .filter(Boolean);
  if (!toks.length) {
    // 全是元字符也危险：fork bomb ':(){:|:&};:' 类
    return /:\s*\(\s*\)\s*\{[^}]*[:|]/.test(rawJoined);
  }
  // ANSI-C/hex 转义载荷（bash $'\x72\x6d…'）→ 静态不可证 → 必标
  if (/\\x[0-9a-f]{2}|\\u[0-9a-f]{4}|\\0[0-7]/i.test(rawJoined)) return true;
  // 命令替换 $(cmd) / `cmd` → 内嵌执行不可静态证安全 → 必标
  if (/\$\s*\(|`/.test(rawJoined)) return true;
  // fork bomb / 函数定义注入
  if (/:\s*\(\s*\)\s*\{/.test(rawJoined)) return true;
  // 环境变量前缀 FOO=bar cmd：赋值 token 不挡后续命令词（全位置扫描已覆盖，
  // 此条只兜底 '$CMD -rf' 式纯变量调用——flags 集合留档即可）
  const flagChars = new Set(), flagWords = new Set(), words = [];
  let sawRedirect = false, pipeToInterp = false;
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i];
    if (t === '>' || t === '<') { sawRedirect = true; continue; }
    if (t === '|' || t === '|&') {
      const nxt = toks[i + 1];
      if (nxt && INTERPRETERS.has(normWord(nxt))) pipeToInterp = true;
      continue;
    }
    if (/^--[a-z][a-z0-9-]*/i.test(t)) flagWords.add(t.slice(2));
    else if (/^-[a-z]{4,}/i.test(t)) flagWords.add(t.slice(1));   // PowerShell/长单横线 flag（-delete/-EncodedCommand）
    else if (/^-[a-z]/i.test(t)) for (const c of t.slice(1)) flagChars.add(c);  // Unix 短 flag 束（-rf）
    else if (/^\/[a-z]$/i.test(t)) flagChars.add(t.slice(1));     // cmd 单字符 /s
    else if (/^\/[a-z][a-z0-9:=.]*$/i.test(t)) flagWords.add(t.slice(1));  // cmd 长 flag（/delete /cleanup /mir）
    else words.push(t);
  }
  if (sawRedirect || pipeToInterp) return true;
  const has = (...cs) => cs.some(c => flagChars.has(c) || flagWords.has(c));
  const forceWord = [...flagWords].some(w => w.startsWith('force'));
  const hasWord = (...ws) => words.some(w => ws.includes(w) || ws.includes(normWord(w)));
  const hasDangerSub = [...words, ...flagWords].some(w => DANGER_SUBS.has(w) || DANGER_SUBS.has(normWord(w)));
  const hasHost = words.some(w => HOST_TOOLS.has(w) || HOST_TOOLS.has(normWord(w)));
  if (hasHost && hasDangerSub) return true;
  // eval/source/iex 带任何参数 = 直接执行 → 必标
  if (hasWord('eval', 'source', 'iex', 'invoke-expression')) return true;
  // 解释器内联代码（python -c / node -e / powershell -Command/-EncodedCommand / sh -c / cmd /c / java -jar / python -m）
  const hasInterp = words.some(w => INTERPRETERS.has(w) || INTERPRETERS.has(normWord(w)));
  if (hasInterp && has('c', 'e', 'm', 'command', 'encodedcommand', 'enc', 'encoded', 'jar', 'f', 'k', 'file', 'eval', 'i')) return true;
  // 解释器+脚本文件（python x.py / node app.js）：'.' 是切词符 →
  // 'script.py' 裂成 script,.,py——必须重组 toks 检测，此条曾是死代码。
  if (hasInterp) {
    for (let i = 0; i + 2 < toks.length; i++) {
      if (toks[i + 1] === '.' && SCRIPT_EXTS.has(toks[i + 2])) return true;
    }
    // 解释器带任何非 flag 实参（python anything / sh script）——无法证安全
    if (words.some((w, i) => (INTERPRETERS.has(w) || INTERPRETERS.has(normWord(w)))
        && words.slice(i + 1).some(x => !CMD_WRAPPERS.has(x) && !INTERPRETERS.has(x)))) return true;
  }
  // LOLBin 任意调用形态
  if (words.some(w => LOLBINS.has(w) || LOLBINS.has(normWord(w)))) return true;
  // ssh/wsl 通道（ssh 在 CMD_WRAPPERS 会被跳过自身检查，隧道/远程执行要单独标）
  if (hasWord('ssh', 'wsl', 'scp', 'sftp', 'mosh')) return true;
  // 无歧义系统态变更命令（任何位置出现即标；常见英文词 at/ln/su/env 不收——
  // 全位置匹配会误报普通文本，且 exec 通道本就 default-irreversible 兜底）
  if (hasWord('crontab', 'schtasks', 'systemctl', 'mount', 'dpkg', 'rpm',
      'chroot', 'unshare', 'nsenter', 'setpriv', 'newgrp', 'busybox',
      'killall', 'pkill', 'useradd', 'groupadd', 'visudo', 'firewall-cmd',
      'launchctl', 'diskutil', 'nvram', 'efibootmgr')) return true;
  // git 子命令矩阵（-c 选项值隔着也扫得到——按词不按位）
  if (hasWord('git')) {
    if (hasWord('push') && (has('f', 'd', 'delete', 'mirror') || forceWord || words.some(w => w.startsWith('+')) || /[\s+]:[a-z0-9]/i.test(rawJoined))) return true;
    if (hasWord('reset') && hasWord('hard')) return true;
    if (hasWord('clean') || hasWord('restore') || hasWord('filter-branch') || hasWord('filter-repo') || hasWord('prune')) return true;
    if (hasWord('rebase') || hasWord('deinit') || hasWord('symbolic-ref')) return true;
    if (hasWord('checkout', 'switch') && (has('f') || hasWord('--'))) return true;
    if (hasWord('commit') && has('amend')) return true;
    if (hasWord('branch', 'update-ref', 'tag') && has('d')) return true;
    if (hasWord('reflog') && hasWord('expire', 'delete')) return true;
    if (hasWord('gc') && [...flagWords].some(w => w.startsWith('prune'))) return true;
    if (hasWord('stash') && hasWord('clear', 'drop')) return true;
    if (hasWord('worktree', 'remote') && hasWord('remove', 'prune')) return true;
    if (hasWord('rm') && has('r', 'f', 'cached')) return true;
  }
  for (let j = 0; j < words.length; j++) {
    const cmd = words[j], ncmd = normWord(cmd), sub = words[j + 1], nsub = sub && normWord(sub);
    if (CMD_WRAPPERS.has(cmd) || CMD_WRAPPERS.has(ncmd)) continue;
    if (DESTRUCTIVE_CMDS.has(cmd) || DESTRUCTIVE_CMDS.has(ncmd) || cmd.startsWith('mkfs')) return true;
    if (cmd.includes('rmtree') || cmd.startsWith('drop') || cmd.startsWith('delete') || cmd.startsWith('destroy')) return true;
    if ((INTERPRETERS.has(cmd) || INTERPRETERS.has(ncmd)) && sub && (SCRIPT_EXTS.has(sub) || SCRIPT_EXTS.has(nsub) || sub === '-')) return true;
    if ((cmd === 'sed' || cmd === 'perl' || cmd === 'awk' || cmd === 'find') && has('i', 'delete', 'exec')) return true;
    if ((cmd === 'cp' || cmd === 'copy' || cmd === 'move' || cmd === 'xcopy' || cmd === 'scp') && (has('f', 'y') || hasWord('/dev/null') || words.length - j >= 3)) return true;
    if ((cmd === 'tar') && has('x', 'w', 'o')) return true;
    if ((cmd === 'unzip' || cmd === 'expand') && has('o', 'f', 'd')) return true;
    if ((cmd === 'curl' || cmd === 'wget' || cmd === 'certutil' || cmd === 'bitsadmin') && (has('o', 'output', 'outfile', 'urlcache', 'decode', 'transfer', 'remote-name') || sawRedirect)) return true;
    if ((cmd === 'chmod' || cmd === 'chown') && (has('r') || hasWord('000', '0000', '777'))) return true;
    if (cmd === 'iptables' || cmd === 'netsh' || cmd === 'ufw') {
      if (has('f', 'x', 'd') || hasWord('flush', 'off', 'delete', 'reset', 'disable')) return true;
    }
    if (cmd === 'drop' || cmd === 'truncate') return true;
  }
  return false;
}
const SEMANTIC_TYPES = new Set(['EPISTEMIC_CLAIM', 'NORMATIVE_DIRECTIVE', 'AUTHORIZATION', 'DURABLE_VALUE_STATEMENT', 'PREFERENCE']);

function today() { return new Date().toISOString().slice(0, 10); }
function safeJson(v) { try { return JSON.stringify(v); } catch { return '"<unserializable>"'; } }
function readJson(p) { try { return existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : null; } catch { return null; } }
function bytes(s) { return Buffer.byteLength(String(s), 'utf8'); }

const WM_PARAMETERS = {
  type: 'object',
  properties: {
    op: { type: 'string', enum: ['activate', 'model', 'predict', 'observe', 'evaluate', 'update', 'probe', 'meta', 'value', 'input', 'persist', 'status', 'declassify'] },
    reason: { type: 'string' },
    mode: { type: 'string', enum: ['core', 'full'] },
    models: { type: 'array', items: { type: 'object', additionalProperties: true } },
    subject: { type: 'string' },
    intended_action: { type: 'string' },
    expected_observation: { type: 'string' },
    falsifier: { type: 'string' },
    time_horizon: { type: 'string' },
    confidence_bucket: { type: 'string', enum: ['high', 'medium', 'low', 'unknown'] },
    irreversible: { type: 'boolean' },
    prediction_id: { type: 'string' },
    observation: { type: 'string' },
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'partial', 'unknown'] },
    evaluation_source: { type: 'string', enum: ['mechanical', 'later_reality', 'independent_model', 'human', 'self'] },
    observation_refs: { type: 'array', items: { type: 'string' } },
    model_id: { type: 'string' },
    revision_type: { type: 'string', enum: ['param', 'structure', 'h4'] },
    change: { type: 'string' },
    update_class: { type: 'string', enum: ['world_model', 'value_model', 'governance_u1', 'governance_u0', 'lineage', 'declassification', 'body_binding', 'gate_policy', 'schema'] },
    target: { type: 'string', enum: ['confirmation', 'discrimination', 'falsification', 'exploration'] },
    probe_mode: { type: 'string', enum: ['understand', 'act'] },
    level: { type: 'string', enum: ['param', 'model', 'representation'] },
    expected_gain: { type: 'string', enum: ['high', 'medium', 'low', 'unknown'] },
    cost: { type: 'string', enum: ['cheap', 'medium', 'expensive', 'unknown'] },
    rejected_alternatives: { type: 'array', items: { type: 'string' } },
    decision: { type: 'string', enum: ['continue', 'query', 'act', 'stop'] },
    bottleneck: { type: 'string', enum: ['information', 'computation', 'model', 'value', 'none', 'unknown'] },
    goal: { type: 'string' },
    decision_criteria: { type: 'string' },
    proxy_risk: { type: 'string' },
    value_update: { type: 'object', additionalProperties: true },
    // INPUT_SEMANTICS (V0.3.1 §6)
    semantic_type: { type: 'string', enum: ['EPISTEMIC_CLAIM', 'NORMATIVE_DIRECTIVE', 'AUTHORIZATION', 'DURABLE_VALUE_STATEMENT', 'PREFERENCE'] },
    content: { type: 'string' },
    source_id: { type: 'string' },
    scope: { type: 'string' },
    action_ref: { type: 'string' },
    // classification (§8)
    access_level: { type: 'string', enum: ['PRIVATE', 'INTERNAL', 'SANITIZED', 'PUBLIC'] },
    epistemic_layer: { type: 'string', enum: ['L0', 'L1', 'L2', 'L3'] },
    declassify_target: { type: 'string' },
    destination: { type: 'string' },
    redaction_manifest: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    open_loops: { type: 'array', items: { type: 'string' } },
    canonical_proposal: { type: 'object', additionalProperties: true },
    evidence_refs: { type: 'array', items: { type: 'string' } },
    supersedes: { type: 'array', items: { type: 'string' } },
    uncertainty_type: { type: 'string', enum: ['empirical', 'logical'] }
  },
  required: ['op'],
  additionalProperties: true
};

export function apply(ctx, config = {}) {
  const wmDir = config.stateDir || join(homedir(), '.dsh', 'world-model');
  // instance-contract-v1: config > WORLD_MODEL_HOME > <instance-root>/world-model/canonical > legacy discovery
  // 产品不预设研究仓路径；研究机用 WORLD_MODEL_HOME 显式指向 pilot。
  const iroot = process.env.PERSONAL_AI_HOME || process.env.PERSONAL_AI_STATE
    || join(homedir(), '.personal-ai');
  const wmDefault = join(iroot, 'world-model', 'canonical');
  const wmLegacy = join(homedir(), 'personal-ai-state', 'world-model');
  const canonicalDir = config.canonicalDir || process.env.WORLD_MODEL_HOME
    || (existsSync(wmDefault) || !existsSync(wmLegacy) ? wmDefault : wmLegacy);
  const bodyId = config.bodyId || `dsh-${hostname()}`;
  // fail-closed: 未知模式回退 core（守卫在场），不能因笔误静默变 off
  const rawMode = String(config.mode || process.env.DSH_WM_MODE || 'off').toLowerCase().trim();
  const envMode = ['off', 'strict-off', 'core', 'full'].includes(rawMode) ? rawMode : 'core';
  for (const d of [join(wmDir, 'ledger'), join(wmDir, 'runs'), join(canonicalDir, 'proposals'), join(canonicalDir, 'history')]) {
    try { mkdirSync(d, { recursive: true }); } catch { /* ignore */ }
  }
  const bodyStatePath = join(wmDir, 'body-state.json');
  let bodyState = readJson(bodyStatePath) || { body_id: bodyId, last_lineage_head: null, epoch_seen: null };
  // 原子写：tmp+rename——中途崩溃留旧文件而非撕裂 JSON
  // （body-state.json 丢失会静默遗忘 epoch/lineage floor = fork 检测失效）。
  function writeAtomic(p, s) {
    const tmp = `${p}.${process.pid}.tmp`;
    writeFileSync(tmp, s);
    renameSync(tmp, p);
  }
  function saveBodyState() { try { writeAtomic(bodyStatePath, safeJson(bodyState)); } catch { /* ignore */ } }

  const sessions = new Map();
  let globalSeq = 0;
  // session id 卫生：非字符串 id 坍缩成 "[object Object]" 会合并会话——哈希区分；
  // 文件路径单独消毒（含 hash 后缀防 '..' / 路径逃逸 / 伪造他人 run 文件）。
  let anonSidShared = null;
  function safeSid(raw) {
    // 事件路径（tools/result、session/event）：非字符串 sid 归到本实例唯一的
    // 随机 anon 桶——不可预测、且只产生一个 run 文件（防事件风暴垃圾）。
    if (typeof raw === 'string' && raw) return raw;
    if (!anonSidShared) anonSidShared = 'anon-' + randomUUID().slice(0, 12);
    return anonSidShared;
  }
  function safeSidStrict(raw) {
    // 执行/守卫路径：非字符串 sid 每调用独立成新会话——畸形 id 永远没有会话
    // 连续性，predict/guard 无法跨调用累积授权（fail closed），也杜绝畸形
    // sid 之间共享预测桶的串话。
    if (typeof raw === 'string' && raw) return raw;
    return 'anon-' + randomUUID().slice(0, 12);
  }
  function sessionFileId(sid) {
    const clean = String(sid).replace(/[^A-Za-z0-9._-]/g, '_') || 'x';
    // 永远带 hash 后缀：大小写不敏感文件系统上 con/CON、以及仅大小写不同
    // 的两个 sid 不会合并 run 文件；全点/空名 sid 也不会变成 dotfile。
    return ('s-' + clean).slice(0, 48) + '-' + createHash('sha256').update(String(sid)).digest('hex').slice(0, 8);
  }
  function sessionFor(exec, shared) {
    const raw = exec?.agent?.session?.id ?? exec?.session?.id;
    const sid = shared ? safeSid(raw) : safeSidStrict(raw);
    if (!sessions.has(sid)) sessions.set(sid, {
      id: sid, mode: envMode, predictions: new Map(), evaluated: new Set(), restored: new Set(),
      bound: new Set(), activated: envMode !== 'off', seq: 0, lastEventId: null, blockedGuards: 0
    });
    // 上限：unbounded sessions Map = 内存/文件句柄 DoS。FIFO 逐出最旧。
    if (sessions.size > 4096) sessions.delete(sessions.keys().next().value);
    return sessions.get(sid);
  }

  // runtime-state.json 是 governance.yaml 的编译投影。区分三种状态：
  //   absent（fresh canonical，bootstrap 合法）/ present（严格用它）/ corrupt（fail-closed）。
  function runtimeState() {
    const p = join(canonicalDir, 'runtime-state.json');
    if (!existsSync(p)) return { _present: false };
    try { return { _present: true, ...JSON.parse(readFileSync(p, 'utf8')) }; }
    catch { return { _present: true, _corrupt: true }; }
  }

  // governance.yaml 的 normative_authorities 是权威源（runtime-state.json 只是投影，
  // 可能缺失/陈旧）。缩进式最小解析：只认 source: → scopes: → scope: level 结构。
  // 缓存带 mtime——operator 中途投放/修改 governance.yaml（撤销权威、
  // 新增 source）必须即时生效；null 不得永久缓存。
  let _govCache;
  function governanceAuthorities() {
    const gp = join(canonicalDir, 'governance.yaml');
    let mtimeMs = null;
    try { mtimeMs = statSync(gp).mtimeMs; } catch { /* absent or unreadable */ }
    if (_govCache && _govCache.mtimeMs === mtimeMs) return _govCache.table;
    const table = _parseGovernance(mtimeMs === null ? null : gp);
    _govCache = { mtimeMs, table };
    return table;
  }
  function _parseGovernance(gp) {
    if (gp === null) return null;   // 文件不存在 → 回退投影/bootstrap 判定
    try {
      // null-proto 表：source 名是攻击面（__proto__/constructor/toString 命中
      // Object.prototype 会崩掉整个权威表 → 静默退回 bootstrap）。own-prop 语义。
      const table = Object.create(null);
      let inBlock = false, cur = null, inScopes = false, found = false;
      // BOM/CRLF 归一：BOM 会让首行缩进错位 → 整个权威节静默失配
      for (const ln of readFileSync(gp, 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)) { // eslint-disable-line
        // 列表式 scope 条目（'    - task_goal'）——bootstrap canonical 的写法，
        // 丢了会让骨架仓连 owner 的 NORMATIVE 都被拒（fail-closed 但不可用）。
        const li = ln.match(/^(\s*)-\s+([^#:]+?)\s*$/);
        if (li && inBlock && inScopes && cur && Object.hasOwn(table, cur) && li[1].length >= 4) {
          table[cur].scopes[li[2].trim()] = 'authoritative';
          continue;
        }
        const m = ln.match(/^(\s*)([^\s#][^:]*):(?:\s*(.*))?$/);
        if (!m) continue;
        const indent = m[1].length, key = m[2].trim();
        // 行内注释：值里 ' # …' 起算注释（YAML 规则：# 前需空白）
        const val = (m[3] || '').replace(/\s+#.*$/, '').trim();
        if (indent === 0) { inBlock = key === 'normative_authorities'; if (inBlock) found = true; cur = null; inScopes = false; continue; }
        if (!inBlock) continue;
        if (indent === 2) { cur = key; inScopes = false; if (!Object.hasOwn(table, cur)) table[cur] = { scopes: Object.create(null) }; continue; }
        if (!cur || !Object.hasOwn(table, cur)) continue;
        // 兄弟键必须重置 inScopes——否则 roles:/notes: 子项被吞进 scopes 造出幻影权威
        if (indent === 4) { inScopes = key === 'scopes'; continue; }
        if (inScopes && indent >= 6) {
          // 'name: authoritative' 或 'name: {level: authoritative}' 内联表 → 取 key 为 scope；
          // 空值（'name:'）当 list-entry 处理 = authoritative
          table[cur].scopes[key] = (val === '' || val.startsWith('{')) ? 'authoritative' : val;
        }
      }
      // 文件存在但无 normative_authorities 节 = 已声明治理但没有权威 → deny-all，
      // 不得静默回退 bootstrap（否则 typo 的节名就把 default-user-root 送出去）
      return found ? table : Object.create(null);
    } catch {
      // 存在但不可读/解析失败（目录、损坏）→ 权威声明失效 = deny-all，
      // 绝不能静默回退 bootstrap（会把 default-user-root 送给自报 user）
      return Object.create(null);
    }
  }

  // 审计可用性：stateDir 不可写 = 审计基础设施失效 → fail closed（所有 op 拒绝），
  // 否则世界模型在"零审计"状态下继续跑 = fail open。
  let auditOk = true;
  try {
    mkdirSync(join(wmDir, 'ledger'), { recursive: true });
    mkdirSync(join(wmDir, 'runs'), { recursive: true });
    appendFileSync(join(wmDir, 'ledger', '.probe'), '');
    appendFileSync(join(wmDir, 'runs', '.probe'), '');
  } catch { auditOk = false; }

  const EV_KEEP_KEYS = new Set(['event_id', 'schema_version', 'theory_version', 'session_id', 'seq', 'prev_event', 'event_type', 'timestamp', 'actor', 'body_id', 'bcc', 'layer', 'access']);
  function emit(sessionId, eventType, data = {}) {
    const s = sessions.get(sessionId);
    const seq = ++globalSeq;
    let ev = {
      event_id: randomUUID(), schema_version: SCHEMA_VERSION, theory_version: THEORY_VERSION,
      session_id: sessionId, seq, prev_event: s ? s.lastEventId : null,
      event_type: eventType, timestamp: new Date().toISOString(), actor: 'agent',
      body_id: bodyId, bcc: BCC_VERSION,
      layer: eventType === 'RAW_EVIDENCE' ? 'L0' : 'L1',
      access: data.access || 'PRIVATE',
      ...data
    };
    try {
      let line = safeJson(ev);
      if (bytes(line) > 16384) {
        // 全行上限：任何顶层字段超限都截——不只白名单（evidence_refs/source_id/
        // channel_id/open_loops 等同样能塞爆行宽）。截断后仍超限 → stub。
        for (const k of Object.keys(ev)) {
          if (EV_KEEP_KEYS.has(k)) continue;
          if (bytes(safeJson(ev[k])) <= 1024) continue;
          ev[k] = (k === 'payload' || typeof ev[k] !== 'string')
            ? { _truncated: true, preview: safeJson(ev[k]).slice(0, 4000) }
            : ev[k].slice(0, 1024) + '…';
        }
        line = safeJson(ev);
        if (bytes(line) > 16384) {
          ev = { event_id: ev.event_id, schema_version: ev.schema_version, theory_version: ev.theory_version, session_id: ev.session_id, seq: ev.seq, prev_event: ev.prev_event, event_type: ev.event_type, timestamp: ev.timestamp, actor: ev.actor, body_id: ev.body_id, bcc: ev.bcc, layer: ev.layer, access: ev.access, happened: 'event truncated: oversized fields dropped', payload: { _truncated: true } };
          line = safeJson(ev);
        }
      }
      appendFileSync(join(wmDir, 'ledger', `${today()}.jsonl`), line + '\n');
      appendFileSync(join(wmDir, 'runs', `${sessionFileId(sessionId)}.jsonl`), line + '\n');
      // prev_event 链只在写盘成功后推进——中途失败不得留下悬空指针
      if (s) s.lastEventId = ev.event_id;
    } catch { auditOk = false; /* ledger write must never crash the loop */ }
    return ev;
  }

  // ---- body lease: single canonical writer per entity (V0.3.1 §11) ----
  function leaseCheck(s) {
    const rs = runtimeState();
    if (rs._corrupt) {
      emit(s.id, 'LEASE_DENIED', { happened: 'runtime-state.json corrupt — fail closed', payload: { requester: bodyId }, source: 'plugin' });
      return { ok: false, holder: 'corrupt-runtime-state' };
    }
    // active_body 存在但畸形（string/0/null/非对象/缺 body_id）= 状态不可信 → fail closed
    const rawActive = rs._present ? rs.active_body : undefined;
    if (rawActive !== undefined && rawActive !== null
        && (typeof rawActive !== 'object' || typeof rawActive.body_id !== 'string' || !rawActive.body_id)) {
      emit(s.id, 'LEASE_DENIED', { happened: 'runtime-state.json active_body malformed — fail closed', payload: { requester: bodyId }, source: 'plugin' });
      return { ok: false, holder: 'malformed-active-body' };
    }
    const active = (rawActive && typeof rawActive === 'object') ? rawActive : {};
    if (!active.body_id) return { ok: true, note: 'no active body bound' };
    // lease 必须恰好是 exclusive-canonical-writer——缺失/expired/近似拼写一律拒绝
    if (active.lease !== 'exclusive-canonical-writer') {
      emit(s.id, 'LEASE_DENIED', { happened: `lease state '${active.lease}' is not an active exclusive-canonical-writer`, payload: { holder: active.body_id, requester: bodyId }, source: 'plugin' });
      return { ok: false, holder: active.body_id };
    }
    if (active.body_id === bodyId) {
      // fork detection：已记录的 head 之后，head 缺失/变化/epoch 回退都 = 不可信 → 拒写。
      // head 必须是 string|null——对象/数字会让 !== 比较每次自叉（自锁死）。
      const head = rs.lineage_head;
      if (head != null && typeof head !== 'string') {
        emit(s.id, 'LEASE_DENIED', { happened: 'lineage_head malformed (non-string) — fail closed', payload: { seen_type: typeof head }, source: 'plugin' });
        return { ok: false, holder: 'lineage-head-malformed' };
      }
      if (bodyState.last_lineage_head && head !== bodyState.last_lineage_head) {
        emit(s.id, 'FORK_DETECTED', { happened: 'canonical lineage_head changed/missing under active lease', payload: { expected: bodyState.last_lineage_head, seen: head }, source: 'plugin' });
        return { ok: false, holder: 'fork-detected' };
      }
      // epoch 必须安全整数——非整数/非数值（"abc"/NaN/1e99/5.5）= 状态不可信 → fail closed，
      // 且绝不写进 epoch_seen（否则毒化 floor 后真实回退也被放行）。
      if (rs.continuity_epoch != null && !Number.isSafeInteger(rs.continuity_epoch)) {
        emit(s.id, 'LEASE_DENIED', { happened: 'continuity_epoch malformed (non-integer) — fail closed', payload: { seen: rs.continuity_epoch }, source: 'plugin' });
        return { ok: false, holder: 'epoch-malformed' };
      }
      const seenEpoch = Number.isSafeInteger(bodyState.epoch_seen) ? bodyState.epoch_seen : null;
      if (seenEpoch != null && rs.continuity_epoch != null) {
        if (rs.continuity_epoch < seenEpoch) {
          emit(s.id, 'LEASE_DENIED', { happened: 'continuity_epoch regressed — state not trusted', payload: { seen: rs.continuity_epoch, expected_min: seenEpoch }, source: 'plugin' });
          return { ok: false, holder: 'epoch-regression' };
        }
        // 前跳荒谬值（5→1e9）也会永久锁死 floor —— 超界即不可信
        if (rs.continuity_epoch > seenEpoch + 1000000) {
          emit(s.id, 'LEASE_DENIED', { happened: 'continuity_epoch implausible forward jump — fail closed', payload: { seen: rs.continuity_epoch, expected_max: seenEpoch + 1000000 }, source: 'plugin' });
          return { ok: false, holder: 'epoch-forward-jump' };
        }
      }
      bodyState.last_lineage_head = head || bodyState.last_lineage_head;
      if (rs.continuity_epoch != null) bodyState.epoch_seen = rs.continuity_epoch;
      saveBodyState();
      return { ok: true };
    }
    emit(s.id, 'LEASE_DENIED', { happened: `canonical write denied: lease held by ${active.body_id}`, payload: { holder: active.body_id, requester: bodyId }, source: 'plugin' });
    return { ok: false, holder: active.body_id };
  }

  // 生命周期保留字段：payload 是调用方控制面，伪造 status/decision/applied
  // 会骗过信任 payload.status 的消费者——写入前一律剥掉。
  const PROPOSAL_RESERVED = /^(status|decision|authority|applied|applied_at|applied_by|applied_model|rollback)$/i;
  function writeProposal(kind, payload, sessionId, s, access) {
    const lease = s ? leaseCheck(s) : { ok: true };
    if (!lease.ok) return { denied: true, holder: lease.holder };
    const clean = {};
    for (const k of Object.keys(payload || {})) {
      if (!PROPOSAL_RESERVED.test(k)) clean[k] = payload[k];
    }
    const body = safeJson({
      schema_version: SCHEMA_VERSION, kind, session_id: sessionId,
      timestamp: new Date().toISOString(), body_id: bodyId,
      classification: { level: access || 'PRIVATE', basis: ['taint_or_default'] },
      payload: clean, status: 'proposed'
    });
    // canonical 提案也有界：无界写入 = 经 canonical 灌任意体积数据
    if (bytes(body) > 256 * 1024) {
      emit(sessionId, 'PROPOSAL_DENIED', { happened: 'proposal exceeds 256KiB cap', payload: { kind }, source: 'plugin' });
      return { denied: true, holder: 'proposal-too-large' };
    }
    const p = join(canonicalDir, 'proposals', `${today()}-${kind}-${randomUUID().slice(0, 8)}.json`);
    try {
      writeFileSync(p, body);
      if (!existsSync(p)) return { denied: true, holder: 'proposal-write-unverified' };
    } catch (e) {
      emit(sessionId, 'LEASE_DENIED', { happened: `proposal write failed: ${e?.message || e}`, payload: { kind }, source: 'plugin' });
      return { denied: true, holder: 'proposal-write-failed' };
    }
    return { path: p };
  }

  function readCanonicalSummary() {
    const cur = join(canonicalDir, 'current.yaml');
    if (!existsSync(cur)) return '(no canonical world-model yet — first activation)';
    try {
      const text = readFileSync(cur, 'utf8');
      return text.length > 1500 ? text.slice(0, 1500) + '\n…(truncated)' : text;
    } catch { return '(canonical unreadable)'; }
  }

  function canonicalStale() {
    try {
      const bp = join(canonicalDir, 'briefing.md');
      const rp = join(canonicalDir, 'runtime-state.json');
      if (!existsSync(bp) || !existsSync(rp)) return 'compiled artifacts missing';
      const bm = statSync(bp).mtimeMs, rm = statSync(rp).mtimeMs;
      let newest = 0;
      for (const f of readdirSync(canonicalDir)) {
        if (!f.endsWith('.yaml')) continue;
        const m = statSync(join(canonicalDir, f)).mtimeMs;
        if (m > newest) newest = m;
      }
      return (bm < newest || rm < newest) ? 'canonical newer than compiled artifacts — re-run canonical_compile.py' : null;
    } catch { return null; }
  }

  function updateCurrentJson(patch) {
    const cur = join(wmDir, 'current.json');
    let state = { schema_version: SCHEMA_VERSION, updated_at: null, open_predictions: [], models: {}, open_loops: [] };
    try { if (existsSync(cur)) state = { ...state, ...JSON.parse(readFileSync(cur, 'utf8')) }; } catch { /* ignore */ }
    Object.assign(state, patch, { updated_at: new Date().toISOString() });
    // 失败必须上报——caller 不得在未落盘时冒称 STATE_PERSISTED（审计与磁盘矛盾）
    try { writeAtomic(cur, safeJson(state)); return { state, ok: true }; }
    catch (e) { return { state, ok: false, error: String(e?.message || e) }; }
  }

  // ---- mechanical RAW_EVIDENCE capture (L0): every tool result, unfiltered ----
  try {
    ctx.on('tools/result', (exec, result) => {
      try {
        // 事件路径走共享 anon 桶（shared=true）——非字符串 sid 的每次事件
        // 若各开新会话+新 run 文件 = 无界文件风暴 → 磁盘写满 → auditOk=false。
        const s = sessionFor(exec, true);
        const text = typeof result === 'string' ? result : safeJson(result);
        emit(s.id, 'RAW_EVIDENCE', {
          subject: exec?.name || 'unknown-tool', happened: `tool ${exec?.name || '?'} returned`,
          payload: String(text).slice(0, 500), evidence_refs: [],
          source: 'tools/result', channel_id: 'tool_result',
          sensor_id: exec?.name || 'unknown',
          tool: { canonical_tool_id: exec?.name || 'unknown', body_tool_id: exec?.name || 'unknown' }
        });
      } catch { /* never crash */ }
    });
  } catch { /* event bus optional */ }

  // ---- session briefing：canonical 自动编译产物（非手写），永远注入 ----
  // 世界模型永远在场；OFF/CORE/FULL 只是形式化深度。简报=给 prior 也给
  // 推翻 prior 的钥匙（每条 current best 带 confidence/scope/counter/falsifier）。
  function buildBriefing() {
    const p = join(canonicalDir, 'briefing.md');
    if (!existsSync(p)) return null;
    try {
      let text = readFileSync(p, 'utf8').trim();
      if (!text) return null;
      if (bytes(text) > BRIEF_HARD_CAP) text = Buffer.from(text, 'utf8').slice(0, BRIEF_HARD_CAP - 60).toString('utf8') + '\n…(hard-capped at 8KiB)';
      return text;
    } catch { return null; }
  }

  // 简报=system-prompt section：永远在场、不竞争 user 消息、每次 prompt
  // assemble 重读 briefing.md（canonical 重编译后自动生效、跨 compaction 存活）。
  let briefingMounted = false;
  try {
    if (ctx.systemPrompt?.section) {
      ctx.systemPrompt.section({
        name: 'world-model:briefing',
        order: 10,
        text: () => buildBriefing() || ''
      });
      briefingMounted = true;
    }
  } catch { /* systemPrompt service optional */ }

  try {
    const briefedSessions = new Set();
    ctx.on('session/event', (session, event) => {
      try {
        const sid = safeSid(session?.id);
        if (briefedSessions.has(sid)) return;
        const s = sessionFor({ agent: { session: { id: sid } } });
        if (event?.type === 'turn/start' || event?.type === 'step/start' || event?.type === 'user/message') {
          briefedSessions.add(sid);
          // 恢复的 open_predictions 记为 known——跨会话 evaluate 合法；未知 pid 不计入 evaluated
          try {
            const prior = readJson(join(wmDir, 'current.json')) || {};
            for (const pid of (Array.isArray(prior.open_predictions) ? prior.open_predictions : [])) s.restored.add(pid);
          } catch { /* restore best-effort */ }
          const stale = canonicalStale();
          emit(s.id, 'STATE_RESTORE', {
            happened: 'world-model state restored into session',
            payload: readCanonicalSummary(), stale, source: 'plugin'
          });
          if (stale) emit(s.id, 'BRIEFING_STALE', { happened: stale, source: 'plugin' });
          if (briefingMounted) {
            emit(s.id, 'BRIEFING_INJECTED', { happened: 'briefing mounted as system-prompt section', payload: { bytes: bytes(buildBriefing() || '') }, source: 'plugin' });
          } else {
            // fallback：无 systemPrompt 服务时退化为 user-message inject
            // （必须在下一 tick——同步 inject 会重入 append publisher）。
            const briefing = buildBriefing();
            if (briefing) {
              setImmediate(() => {
                try {
                  const agentsSvc = ctx.get?.('agents') || ctx.agents;
                  const agent = agentsSvc?.get?.(sid);
                  const msg = { id: randomUUID(), role: 'user', content: [{ type: 'text', text: briefing }], source: { kind: 'plugin', plugin: name } };
                  if (agent?.inject) { agent.inject(msg); emit(s.id, 'BRIEFING_INJECTED', { happened: 'session briefing injected', payload: { bytes: bytes(briefing) }, source: 'plugin' }); }
                  else if (agent?.followup) { agent.followup(msg); emit(s.id, 'BRIEFING_INJECTED', { happened: 'session briefing injected via followup', payload: { bytes: bytes(briefing) }, source: 'plugin' }); }
                  else emit(s.id, 'BRIEFING_FAILED', { happened: 'no agent handle for briefing injection', source: 'plugin' });
                } catch (e) { emit(s.id, 'BRIEFING_FAILED', { happened: `briefing injection threw: ${e?.message || e}`, source: 'plugin' }); }
              });
            }
          }
        }
      } catch { /* never crash */ }
    });
  } catch { /* event bus optional */ }

  // ---- world_model tool ----
  const reg = ctx?.tools?.register;
  if (typeof reg === 'function' && envMode !== 'strict-off') {
    reg.call(ctx.tools, {
      name: 'world_model',
      description: '世界模型状态机（theory V0.4 / schema 1.2）：activate/model/predict/observe/evaluate/update/probe/meta/value/input/persist/status/declassify。所有调用真实落盘 ledger——审计对象不是 prose。consequential 改动前必须先 predict（绑定 intended_action）。input 路由：外部输入先经 semantic_type 分类（会话自身任务指令不必路由）。EPISTEMIC_CLAIM=对世界的断言→W 证据；AUTHORIZATION=一次性行动许可→仅本会话记录；NORMATIVE_DIRECTIVE=要求持久约束的规则→查 governance 权威表（source_id 与 scope 必须用 governance.yaml normative_authorities 里已注册的值，如 user/task_goal，自造词汇会被拒）；DURABLE_VALUE_STATEMENT=持久价值→value proposal；PREFERENCE=本地偏好→不持久化。',
      parameters: WM_PARAMETERS,
      output: { schema: { type: 'object', additionalProperties: true }, render: (_a, v) => [{ type: 'text', text: safeJson(v) }] },
      execute: (input, exec) => {
        try {
        if (!input || typeof input !== 'object' || Array.isArray(input)) input = {};
        const s = sessionFor(exec);
        const op = String(input.op || '');
        // 审计不可用 = fail closed：任何会落账的操作在零审计状态下都拒绝
        if (!auditOk && op !== 'status') return { ok: false, code: 'AUDIT_UNAVAILABLE', message: 'stateDir unwritable — audit infrastructure down, ops refused (fail closed)' };
        const base = { subject: input.subject, evidence_refs: input.evidence_refs || [] };
        switch (op) {
          case 'activate': {
            // off/strict-off 由 env/config 决定——会话不可自行关闸；activate 只能维持或升级形式化深度
            if (input.mode === 'core' || input.mode === 'full') s.mode = input.mode;
            s.activated = true;
            const lease = leaseCheck(s);
            emit(s.id, 'WM_ACTIVATE', { ...base, happened: `world-model activated mode=${s.mode}`, payload: { mode: s.mode, reason: input.reason, lease } });
            return { ok: true, mode: s.mode, lease, note: 'WM active (schema 1.2 / V0.4). Before consequential mutations: world_model(op:"predict") with intended_action binding.' };
          }
          case 'model': {
            const modelList = Array.isArray(input.models) ? input.models : [];
            const ids = modelList.map(m => (m && typeof m === 'object' ? (m.id || m.model_id || m.proposition) : null) || 'unnamed');
            // access taint: model entries inherit strictest level of their evidence; default PRIVATE
            for (const m of modelList) {
              if (m && typeof m === 'object' && !m.access) m.access = { level: input.access_level || 'PRIVATE', basis: ['taint_or_default'] };
            }
            const cur = join(wmDir, 'current.json');
            let st = {};
            try { if (existsSync(cur)) st = JSON.parse(readFileSync(cur, 'utf8')); } catch { /* ignore */ }
            // null-proto 表：id '__proto__' 这类键不能污染原型/被静默吞掉
            const models = Object.assign(Object.create(null), st.models || {});
            for (const m of modelList) {
              if (m && typeof m === 'object') models[String(m.id || m.model_id || 'unnamed')] = m;
            }
            // 先落盘再落账——写失败不得冒称 MODEL_CREATED（审计与磁盘矛盾）
            const wr = updateCurrentJson({ models });
            if (!wr.ok) {
              emit(s.id, 'MODEL_WRITE_FAILED', { ...base, happened: `model write failed: ${wr.error}`, payload: { error: wr.error } });
              return { ok: false, code: 'STATE_WRITE_FAILED', error: wr.error };
            }
            emit(s.id, 'MODEL_CREATED', { ...base, happened: `${ids.length} model(s) registered`, payload: { models: modelList }, epistemic_layer: input.epistemic_layer || 'L2' });
            return { ok: true, model_ids: ids };
          }
          case 'predict': {
            // cap 只数开放预测——evaluated 条目不耗额度（否则长会话永久锁死
            // predict = 自我 DoS）。bound 仍算 open（已授权未评估）。
            let openCount = 0;
            for (const [pid] of s.predictions) if (!s.evaluated.has(pid)) openCount++;
            if (openCount >= 1024) return { ok: false, code: 'PREDICTION_LIMIT', limit: 1024 };
            const pid = `P-${randomUUID().slice(0, 8)}`;
            const closeSuperseded = (old) => {
              if (!s.predictions.has(old) || s.evaluated.has(old)) return;
              s.evaluated.add(old);
              emit(s.id, 'PREDICTION_EVALUATED', { ...base, prediction_id: old, happened: 'verdict=superseded', payload: { verdict: 'unknown', superseded_by: pid, residual: 'superseded by newer prediction' } });
            };
            const supersedesList = Array.isArray(input.supersedes) ? input.supersedes : (typeof input.supersedes === 'string' && input.supersedes ? [input.supersedes] : []);
            for (const old of supersedesList) closeSuperseded(String(old));
            const ia = String(input.intended_action || '');
            // 一预测一工具：intended_action 枚举多个 consequential 工具名 =
            // 批发式授权（一次 predict 放行整类动作）→ 拒绝。逐个 predict。
            const iaToolHits = new Set();
            if (ia) {
              const iaLow = ia.toLowerCase();
              for (const name of CONSEQUENT_TOOLS) {
                const pat = [...name].map(c => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[-_\\s]?');
                if (new RegExp(`(^|[^a-z0-9])${pat}([^a-z0-9]|$)`).test(iaLow)) iaToolHits.add(name);
              }
              // 嵌套名去重：'editor' 命中 'str_replace_editor' 的尾部 token 不算
              // 第二个工具——只有词根互不包含的独立名才算多工具枚举
              for (const h of [...iaToolHits]) {
                for (const o of iaToolHits) {
                  if (o !== h && (o.endsWith(h) || o.startsWith(h))) iaToolHits.delete(h);
                }
              }
            }
            if (iaToolHits.size > 1) {
              return { ok: false, code: 'PREDICTION_TOO_BROAD', tools: [...iaToolHits], message: 'one prediction binds one tool — issue separate predict calls' };
            }
            const subj = String(input.subject || '');
            for (const [old, p] of s.predictions) {
              if (s.evaluated.has(old)) continue;
              const same = (ia && String(p.intended_action || '') === ia) || (!ia && subj && String(p.subject || '') === subj);
              if (same) closeSuperseded(old);
            }
            const rec = {
              prediction_id: pid, subject: input.subject, intended_action: input.intended_action,
              expected_observation: input.expected_observation, falsifier: input.falsifier,
              time_horizon: input.time_horizon, confidence_bucket: input.confidence_bucket,
              irreversible: input.irreversible === true, model_id: input.model_id,
              uncertainty_type: input.uncertainty_type
            };
            s.predictions.set(pid, rec);
            emit(s.id, 'PREDICTION_CREATED', { ...base, prediction_id: pid, model_id: input.model_id, happened: `prediction ${pid}`, payload: rec });
            return { ok: true, prediction_id: pid };
          }
          case 'observe': {
            // prediction_id 声称关联必须可证：未知 pid 或外属 pid → 拒绝（防伪造关联）
            if (input.prediction_id != null && input.prediction_id !== '') {
              const pid = input.prediction_id;
              if (!s.predictions.has(pid)) {
                if (!s.restored.has(pid)) return { ok: false, code: 'UNKNOWN_PREDICTION', prediction_id: pid };
                const prior = readJson(join(wmDir, 'current.json')) || {};
                const owner = Object.hasOwn(prior.prediction_owners || {}, pid) ? prior.prediction_owners[pid] : undefined;
                if (owner !== s.id) return { ok: false, code: 'FOREIGN_PREDICTION', prediction_id: pid, owner: owner || 'unowned' };
              }
            }
            emit(s.id, 'OBSERVATION_RECORDED', { ...base, prediction_id: input.prediction_id, happened: 'observation linked', payload: { observation: input.observation, source: input.source }, source_id: input.source_id, channel_id: input.channel_id, sensor_id: input.sensor_id });
            return { ok: true };
          }
          case 'evaluate': {
            if (typeof input.prediction_id !== 'string' || !input.prediction_id) {
              return { ok: false, code: 'MISSING_PREDICTION_ID' };
            }
            const pred = s.predictions.get(input.prediction_id);
            // 只有本会话创建、或本会话恢复且未被其他会话拥有的 pid 才可判——
            // 未知/外属 pid 计入 evaluated 会让 persist 删掉他人仍 open 的预测（跨会话杀伤）。
            // ownerless 的恢复 pid（旧 current.json 或被剥 owners）= 来源不可证 → fail closed。
            let known = pred !== undefined || s.restored.has(input.prediction_id);
            if (known && s.restored.has(input.prediction_id) && !pred) {
              const prior = readJson(join(wmDir, 'current.json')) || {};
              const owner = Object.hasOwn(prior.prediction_owners || {}, input.prediction_id)
                ? prior.prediction_owners[input.prediction_id] : undefined;
              if (owner !== s.id) {
                return { ok: false, code: 'FOREIGN_PREDICTION', prediction_id: input.prediction_id, owner: owner || 'unowned' };
              }
            }
            if (!known) return { ok: false, code: 'UNKNOWN_PREDICTION', prediction_id: input.prediction_id };
            if (input.verdict !== undefined && !['confirmed', 'refuted', 'partial', 'unknown'].includes(input.verdict)) {
              return { ok: false, code: 'BAD_VERDICT', allowed: ['confirmed', 'refuted', 'partial', 'unknown'] };
            }
            if (input.evaluation_source !== undefined && !['mechanical', 'later_reality', 'independent_model', 'human', 'self'].includes(input.evaluation_source)) {
              return { ok: false, code: 'BAD_EVALUATION_SOURCE' };
            }
            if (input.prediction_id) s.evaluated.add(input.prediction_id);
            emit(s.id, 'PREDICTION_EVALUATED', { ...base, prediction_id: input.prediction_id, happened: `verdict=${input.verdict}`, payload: { verdict: input.verdict, observation_refs: input.observation_refs, residual: input.reason, evaluation_source: input.evaluation_source || 'self' } });
            return { ok: true, prior: pred ? 'bound' : (s.restored.has(input.prediction_id) ? 'restored' : 'unbound'), verdict: input.verdict };
          }
          case 'update': {
            // 审计顺序：先验租约（失败只落 LEASE_DENIED），再落成功事件
            const lease = leaseCheck(s);
            if (!lease.ok) return { ok: false, code: 'LEASE_DENIED', holder: lease.holder };
            // kind 与 update_class 一致：u0/u1 提案走对应评审通道，不出现
            // kind=model-update 而 class=governance_u0 的类别混淆。
            const uclass = String(input.update_class || 'world_model');
            // 封闭词表：update_class → kind 固定映射。任意字符串不得成为提案 kind
            // （逃避类属评审），且 world_model 类必须发 u1_accept.py 可消费的
            // MODEL_PROPOSAL + payload.candidate——否则提案永远进不了 apply 通道。
            const KIND_BY_CLASS = {
              world_model: 'MODEL_PROPOSAL', value_model: 'value-update',
              governance_u0: 'governance-u0', governance_u1: 'governance-u1',
              lineage: 'lineage-update', declassification: 'declassification',
              body_binding: 'body-binding', gate_policy: 'gate-policy', schema: 'schema-update'
            };
            const pkind = KIND_BY_CLASS[uclass];
            if (!pkind) return { ok: false, code: 'BAD_UPDATE_CLASS', allowed: Object.keys(KIND_BY_CLASS) };
            const r = writeProposal(pkind, {
              model_id: input.model_id, revision_type: input.revision_type, change: input.change,
              reason: input.reason, update_class: uclass,
              // candidate 形态对齐 u1_accept.py 消费契约（candidate_id/proposition/…）
              candidate: { candidate_id: input.model_id || `M-${randomUUID().slice(0, 8)}`, proposition: input.change, revision_type: input.revision_type, falsifier: input.falsifier }
            }, s.id, s);
            if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
            // 审计顺序：proposal 落盘成功后才允许出现 MODEL_UPDATED
            emit(s.id, 'MODEL_UPDATED', { ...base, model_id: input.model_id, prediction_id: input.prediction_id, happened: `revision=${input.revision_type}`, payload: { revision_type: input.revision_type, change: input.change, reason: input.reason, supersedes: input.supersedes, update_class: input.update_class || 'world_model', proposal: r.path } });
            return { ok: true, canonical_proposal: r.path };
          }
          case 'probe': {
            emit(s.id, 'PROBE_PLANNED', { ...base, happened: `probe target=${input.target}`, payload: { target: input.target, probe_mode: input.probe_mode, level: input.level, expected_gain: input.expected_gain, cost: input.cost, rejected_alternatives: input.rejected_alternatives } });
            return { ok: true };
          }
          case 'meta': {
            emit(s.id, 'META_DECISION', { ...base, happened: `decision=${input.decision}`, payload: { decision: input.decision, bottleneck: input.bottleneck, rationale: input.reason } });
            return { ok: true };
          }
          case 'value': {
            let proposal;
            if (input.value_update) {
              const lease = leaseCheck(s);
              if (!lease.ok) return { ok: false, code: 'LEASE_DENIED', holder: lease.holder };
              const r = writeProposal('value-update', { ...input.value_update, status: 'proposed', update_class: 'value_model' }, s.id, s);
              if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
              proposal = r.path;
            }
            // VALUE_DECISION 在 proposal 成功之后落账（若本调用带了 value_update）
            emit(s.id, 'VALUE_DECISION', { ...base, happened: 'value/decision recorded', payload: { goal: input.goal, decision_criteria: input.decision_criteria, proxy_risk: input.proxy_risk } });
            return proposal ? { ok: true, value_proposal: proposal } : { ok: true };
          }
          case 'input': {
            // INPUT_SEMANTICS routing (V0.3.1 §6)：输入先分类，不直接全当 evidence
            const st = String(input.semantic_type || '');
            if (!SEMANTIC_TYPES.has(st)) return { ok: false, code: 'BAD_SEMANTIC_TYPE', allowed: [...SEMANTIC_TYPES] };
            const src = String(input.source_id || 'user');
            // scope 是自标分类（audit 级路由字段）：权威检查绑定 source→声称 scope，
            // 不验证内容语义。下游消费者若按 scope 执法需自行做 content 校验。
            const scope = String(input.scope || 'task_goal');
            const content = String(input.content || '');
            if (st === 'EPISTEMIC_CLAIM') {
              emit(s.id, 'INPUT_ROUTED', { ...base, happened: `EPISTEMIC_CLAIM from ${src}`, payload: { semantic_type: st, source_id: src, content: content.slice(0, 500), routed_to: 'world_model_evidence', disagreement_allowed: true }, source_id: src });
              return { ok: true, routed: 'world_model_evidence', note: 'epistemic claim recorded as evidence; principled disagreement permitted if direct evidence is stronger — record via evaluate/update' };
            }
            if (st === 'NORMATIVE_DIRECTIVE') {
              // 权威源优先 governance.yaml；无该文件才退到投影；两者皆无 = fresh bootstrap
              const gov = governanceAuthorities();
              let table, tablePresent;
              if (gov) { table = gov; tablePresent = true; }
              else {
                const rs = runtimeState();
                if (rs._corrupt) {
                  emit(s.id, 'NORMATIVE_DENIED', { ...base, happened: 'normative denied: runtime-state corrupt (fail closed)', payload: { source_id: src, scope }, source_id: src });
                  return { ok: false, code: 'NORMATIVE_DENIED', scope, authority: 'corrupt-runtime-state' };
                }
                table = rs.normative_authorities || {};
                // rs 存在 = canonical 已编译过 → 权威表以它为准（空表=已声明无权威→deny）；
                // bootstrap 逃生门只在 rs 也不存在（真正 fresh canonical）时开
                tablePresent = rs._present;
              }
              const entry = Object.hasOwn(table, src) ? table[src] : null;
              const auth = (((entry || {}).scopes || {})[scope]);
              // default-user-root 只在没有任何权威声明（fresh canonical bootstrap）时兜底；
              // 权威表存在 → 严格查表，自报 source_id 不能凭空获得权威
              if (auth === 'authoritative' || (!tablePresent && src === 'user')) {
                emit(s.id, 'CONSTRAINT_ACCEPTED', { ...base, happened: `normative directive accepted scope=${scope}`, payload: { source_id: src, scope, authority: auth || 'default-user-root', content: content.slice(0, 500) }, source_id: src });
                return { ok: true, routed: 'constraint', scope, authority: auth || 'default-user-root' };
              }
              // authoritative-via-value-proposal 是中介权威——不能直接落地为约束，
              // 只能产出 value proposal 走治理路径
              if (auth === 'authoritative-via-value-proposal') {
                const lease = leaseCheck(s);
                if (!lease.ok) return { ok: false, code: 'LEASE_DENIED', holder: lease.holder };
                const r = writeProposal('value-update', { directive: content, source_id: src, scope, authority: auth, status: 'proposed', update_class: 'value_model' }, s.id, s);
                if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
                emit(s.id, 'INPUT_ROUTED', { ...base, happened: `mediated-authority directive → value proposal`, payload: { source_id: src, scope, proposal: r.path }, source_id: src });
                return { ok: true, routed: 'value_update_proposal', proposal: r.path, authority: auth };
              }
              emit(s.id, 'NORMATIVE_DENIED', { ...base, happened: `normative directive denied: ${src} lacks authority on ${scope}`, payload: { source_id: src, scope, authority: auth || 'none', content: content.slice(0, 300) }, source_id: src });
              return { ok: false, code: 'NORMATIVE_DENIED', scope, authority: auth || 'none' };
            }
            if (st === 'AUTHORIZATION') {
              // AUTHORIZATION 也要查权威——无权威的 source 自报许可不能留下成功记录
              const govA = governanceAuthorities();
              let tableA, tablePresentA;
              if (govA) { tableA = govA; tablePresentA = true; }
              else {
                const rs = runtimeState();
                if (rs._corrupt) {
                  emit(s.id, 'AUTHORIZATION_DENIED', { ...base, happened: 'authorization denied: runtime-state corrupt (fail closed)', payload: { source_id: src, scope }, source_id: src });
                  return { ok: false, code: 'AUTHORIZATION_DENIED', scope, authority: 'corrupt-runtime-state' };
                }
                tableA = rs.normative_authorities || {};
                tablePresentA = rs._present;
              }
              const entryA = Object.hasOwn(tableA, src) ? tableA[src] : null;
              const authA = (((entryA || {}).scopes || {})[scope]);
              if (authA !== 'authoritative' && !(!tablePresentA && src === 'user')) {
                emit(s.id, 'AUTHORIZATION_DENIED', { ...base, happened: `authorization denied: ${src} lacks authority on ${scope}`, payload: { source_id: src, scope, authority: authA || 'none' }, source_id: src });
                return { ok: false, code: 'AUTHORIZATION_DENIED', scope, authority: authA || 'none' };
              }
              emit(s.id, 'AUTHORIZATION_RECORDED', { ...base, happened: `authorization ${src} → ${input.action_ref || scope}`, payload: { source_id: src, scope, action_ref: input.action_ref, content: content.slice(0, 300) }, source_id: src });
              return { ok: true, routed: 'session_authorization', authority: authA || 'default-user-root', note: 'action-scoped permission; NOT a durable value' };
            }
            if (st === 'DURABLE_VALUE_STATEMENT') {
              const lease = leaseCheck(s);
              if (!lease.ok) return { ok: false, code: 'LEASE_DENIED', holder: lease.holder };
              const r = writeProposal('value-update', { durable_value: content, source_id: src, authorization_ref: input.action_ref || `input:${s.id}`, update_class: 'value_model', status: 'proposed' }, s.id, s);
              if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
              emit(s.id, 'INPUT_ROUTED', { ...base, happened: `DURABLE_VALUE_STATEMENT → value proposal`, payload: { source_id: src, proposal: r.path }, source_id: src });
              return { ok: true, routed: 'value_update_proposal', proposal: r.path };
            }
            emit(s.id, 'PREFERENCE_RECORDED', { ...base, happened: `preference (local/non-durable)`, payload: { source_id: src, scope, content: content.slice(0, 300) }, source_id: src });
            return { ok: true, routed: 'local_context', note: 'PREFERENCE affects current context only; not persisted as durable value' };
          }
          case 'declassify': {
            // Declassification proposal (§8)：只产出提案，不改原件等级
            if (!input.declassify_target) return { ok: false, code: 'NEED_TARGET' };
            const lease = leaseCheck(s);
            if (!lease.ok) return { ok: false, code: 'LEASE_DENIED', holder: lease.holder };
            const r = writeProposal('declassification', {
              target: input.declassify_target, destination: input.destination,
              redaction_manifest: input.redaction_manifest || [], update_class: 'declassification',
              flow: 'PRIVATE artifact → sanitizer/redactor → leakage check → authority review → derivative; original keeps its level', status: 'proposed'
            }, s.id, s);
            if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
            emit(s.id, 'DECLASSIFICATION_PROPOSED', { ...base, happened: `declassify ${input.declassify_target}`, payload: { proposal: r.path, destination: input.destination } });
            return { ok: true, proposal: r.path };
          }
          case 'persist': {
            // canonical_proposal 带租约语义——提案先落盘，成功才提交 current.json，
            // STATE_PERSISTED 最后落账；任何一步失败不留半成品状态
            let proposal;
            if (input.canonical_proposal) {
              const lease = leaseCheck(s);
              if (!lease.ok) return { ok: false, code: 'LEASE_DENIED', holder: lease.holder };
              const r = writeProposal('canonical', input.canonical_proposal, s.id, s);
              if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
              proposal = r.path;
            }
            const cur = join(wmDir, 'current.json');
            let prior = {};
            try { if (existsSync(cur)) prior = JSON.parse(readFileSync(cur, 'utf8')); } catch { /* ignore */ }
            const merged = new Set(Array.isArray(prior.open_predictions) ? prior.open_predictions : []);
            const owners = Object.assign(Object.create(null), (prior.prediction_owners && typeof prior.prediction_owners === 'object' ? prior.prediction_owners : {}));
            for (const [pid] of s.predictions) { if (!s.evaluated.has(pid)) { merged.add(pid); owners[pid] = s.id; } }
            for (const pid of s.evaluated) { merged.delete(pid); delete owners[pid]; }
            const patch = { open_predictions: [...merged], prediction_owners: owners, last_summary: input.summary || prior.last_summary };
            if (Array.isArray(input.open_loops)) patch.open_loops = input.open_loops;
            const st = updateCurrentJson(patch);
            if (!st.ok) {
              // 合并丢失却落 STATE_PERSISTED = 审计与磁盘矛盾——如实落失败事件
              emit(s.id, 'STATE_PERSIST_FAILED', { ...base, happened: `state write failed: ${st.error}`, payload: { error: st.error, proposal } });
              return { ok: false, code: 'STATE_WRITE_FAILED', error: st.error };
            }
            emit(s.id, 'STATE_PERSISTED', { ...base, happened: 'state persisted', payload: { summary: input.summary, open_loops: input.open_loops, proposal } });
            return proposal ? { ok: true, state: st.state, canonical_proposal: proposal } : { ok: true, state: st.state };
          }
          case 'status': {
            // 单信任域假设：DSH 实例所有会话同信任级（含子代理会话）——
            // status/persist/briefing 返回全局状态是故意的（agent 需要看到
            // 自己的 open_predictions）。若未来会话分信任级，这里要加访问检查。
            const st = readJson(join(wmDir, 'current.json')) || {};
            const rs = runtimeState();
            return { ok: true, mode: s.mode, session_predictions: [...s.predictions.keys()], current: st, identity: rs.identity || null, lineage_head: rs.lineage_head || null, epoch: rs.continuity_epoch || null, body_id: bodyId, bcc: BCC_VERSION };
          }
          default:
            return { ok: false, code: 'UNKNOWN_OP', message: `unknown op ${op}` };
        }
        } catch (e) { return { ok: false, code: 'INTERNAL_ERROR', message: String(e?.message || e) }; }
      }
    });
  }

  // ---- hard gate: consequential mutation requires bound prediction (CORE/FULL) ----
  try {
    ctx.tools.guard((execution) => {
      try {
        const rawName = String(execution?.name || '');
        const toolName = normToolName(rawName);
        const s = sessionFor(execution);
        if (s.mode !== 'core' && s.mode !== 'full') return undefined;
        if (!toolName) {
          emit(s.id, 'GUARD_BLOCKED', { subject: '(unnamed)', happened: 'blocked unnamed tool call — fail closed' });
          return '[dsh-world-model] BLOCKED: unnamed tool call cannot bind a prediction (fail closed)';
        }
        if (!isConsequential(rawName)) return undefined;
        const irreversible = isIrreversibleByDefault(rawName)
          || isIrreversibleArgs(execution?.arguments ?? execution?.args ?? execution?.params ?? execution?.input ?? {});
        // arg 绑定：调用参数里至少一个 ≥2 字符的 token 必须出现在
        // intended_action 词表里——"predict exec ls" 不能授权 "exec rm -rf /"。
        const argToks = flattenStrings(execution?.arguments ?? execution?.args ?? execution?.params ?? execution?.input ?? {}, [])
          .flatMap(v => String(v).toLowerCase().split(/[^a-z0-9]+/))
          .filter(t => t.length >= 2);
        for (const [pid, p] of s.predictions) {
          if (s.evaluated.has(pid)) continue;   // superseded/evaluated prediction cannot authorize
          if (s.bound.has(pid)) continue;        // 1:1 消耗——一张预测只放行一个动作
          const ia = String(p.intended_action || '');
          if (!ia) continue;
          // 精确绑定：intended_action 里该工具名必须是独立 token（词边界），
          // 分隔符 -_ 可选等价。子串不算——"credit"/"overwrite" 不再误中 edit/write。
          const pat = [...toolName].map(c => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[-_\\s]?');
          const bound = new RegExp(`(^|[^a-z0-9])${pat}([^a-z0-9]|$)`).test(ia.toLowerCase());
          if (!bound) continue;
          if (irreversible && p.irreversible !== true) continue;
          if (argToks.length) {
            const iaToks = new Set(ia.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
            if (!argToks.some(t => iaToks.has(t))) continue;
          }
          s.bound.add(pid);   // 消耗：本预测已授权一个动作，evaluate 语义不受影响
          return undefined; // bound prediction exists → allow
        }
        // 节流：同一会话前 64 次全记，之后每 16 次记一次——阻断风暴不灌爆 ledger
        s.blockedGuards = (s.blockedGuards || 0) + 1;
        if (s.blockedGuards <= 64 || s.blockedGuards % 16 === 0) {
          emit(s.id, 'GUARD_BLOCKED', { subject: toolName, happened: `blocked ${toolName} (no bound prediction)`, payload: { irreversible, suppressed_count: s.blockedGuards } });
        }
        return `[dsh-world-model] BLOCKED: ${toolName} is a consequential action in ${s.mode} mode. First call world_model(op:"predict") with intended_action naming this tool AND its target arguments${irreversible ? ' and irreversible:true (irreversible pattern detected)' : ''}.`;
      } catch {
        // fail closed：守卫自身异常时，consequential 工具拒绝放行
        try {
          if (isConsequential(execution?.name)) return '[dsh-world-model] BLOCKED: guard internal error (fail closed)';
        } catch { /* fall through */ }
        return undefined;
      }
    });
  } catch { /* guard optional */ }
}
