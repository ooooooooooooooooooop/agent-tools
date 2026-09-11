# CASE 1: DSH Workspace Registry Integrity Investigation (Root-Cause Investigation)

## 1. Case Metadata
- **Case ID**: `CASE_01_ROOT_CAUSE_WORKSPACE_INTEGRITY`
- **Archetype**: Root-cause investigation
- **Historical Occurrence**: 2026-09-04 07:47:00+08:00
- **Primary Source Artifacts**: `docs/dsh-workspace-registry-integrity-incident-handoff.md`, `~/.dsh/storages/workspace.json.bak-20260904`, `dsh/workspace-registry-root-fix/test/storage-lost-update.test.mjs`

---

## 2. Frozen Case Input Packet (Pre-Cutoff Facts Only)

### USER_GOAL
"DSH 启动直接崩溃了，报错：
`workspace domain is inconsistent: workspace 'c1e48f21-7910-482a-9f5b-3ee026949787' is absent from registry order`
查清为什么会出现这个不一致？到底是谁写坏的？不要简单删掉或者把数组补齐应付过去，必须找出根本原因并提出防御机制，确保后续多窗口或重启时绝不再发生。"

### TIME_CUTOFF
`2026-09-04T07:47:00+08:00`

### KNOWN_FACTS_AT_CUTOFF
1. DSH Web 启动脚本在执行时抛出致命异常，导致前端界面打不开。
2. 异常栈指向 `@deepseek-ai/dsh-workspace` 的 `workspace domain is inconsistent: workspace 'c1e48f21-7910-482a-9f5b-3ee026949787' is absent from registry order`。
3. `~/.dsh/storages/workspace.json` 文件大小为 39,446 字节，包含 9 个 workspace 记录，但 `global.workspaceIds` 仅有 8 个 ID。
4. 孤立记录为 `c1e48f21-7910-482a-9f5b-3ee026949787`，其路径为 `C:\Desktop\共享\wurank.top`，包含 3 个 session。
5. 另一个旧 workspace 记录 `a9d1651f-9d51-4676-b041-de07e400e1d6` 拥有完全相同的路径 `C:\Desktop\共享\wurank.top`，包含不同的 session 集合。

### AVAILABLE_EVIDENCE_AT_CUTOFF
1. 异常崩溃日志输出（stderr/terminal）。
2. 损坏的存储文件快照：`workspace.json`（已备份为 `workspace.json.bak-20260904`）。
3. DSH 源码中 `@deepseek-ai/dsh-workspace` 和 `@deepseek-ai/dsh-storage-json` 的实现。
4. 本地最近进程启动脚本：`scripts/aic/dsh_desktop_restart.ps1` 和 `dsh-launch-web.ps1`。

### REPO_STATE
- `C:\Users\admin\Desktop\skills` 在 `main` 分支，HEAD 为 Phase 8 之后的治理提交。
- `workspace-registry-phase1` 分支尚未创建。
- 未编写任何关于 lost-update 的单元测试。

### RUNTIME_STATE
- 宿主机系统：Windows 11 AMD64。
- DSH 版本：`base-dsh-0.1.1-rc.2`。
- 端口 3080 处于异常残留或关闭状态。

### USER_CONSTRAINTS
1. 禁止丢失 `c1e48f21` 中关联的 3 个历史会话数据。
2. 禁止为了消除报错而直接采用“只把缺少的一项塞进 order”的浅层热补丁，必须查明机制。
3. 不能修改 DSH 只读 upstream 源码，只能通过外层包装、脚本门禁或 Cordis patch 提供防御。

### AVAILABLE_TOOLS
- File inspection: `read`, `write`, `edit`, `glob`, `grep`
- Shell execution: `pwsh`
- Task state: `todo_write`

### KNOWN_DECISIONS
- 已将原始损坏文件安全备份至 `workspace.json.bak-20260904`。

### KNOWN_UNCERTAINTIES
1. 究竟是多进程并发写导致，还是单个进程在创建 workspace 时崩溃中断导致？
2. `workspace.json` 底层存储机制是否有跨进程锁（Lockfile）？
3. `a9d1651f` 和 `c1e48f21` 为何会拥有同一个路径？是否是重复创建？

---

## 3. Acceptance Design

### DETERMINISTIC_ACCEPTANCE
1. **Data Preservation**: 经修复后的 `workspace.json` 能够被 DSH `base-dsh-0.1.1-rc.2` 正确解析，`records.length === order.length` 严格成立，且原有 3 个会话完整归属于该路径的主 workspace 下。
2. **Deterministic Startup**: 执行 DSH 启动命令后，无 `inconsistent` 异常抛出，端口 3080 成功就绪并返回 HTTP 200。
3. **Reproducible Proof**: 能够写出一段最小复现脚本（或测试用例），证明只要存在多进程或并发写，就会触发 lost-update 或非原子交错写。
4. **Defensive Gating**: 在启动前或同步检查中增加单例进程守卫（Single Instance Guard）或完整性校验器，阻止在端口未释放时强行二次启动。

### SEMANTIC_OUTCOME_ACCEPTANCE
- 正确识别并解释出根本机制：`@deepseek-ai/dsh-storage-json` 依赖内存快照全量写，且无跨进程文件锁，与 `createCanonical` 的非原子分步写（先 put table 再 setState order）并发交错。
- 允许系统给出比历史实现更好的解决方案（例如：OS 文件锁、无锁只追加 journal、或启动互斥锁）。

---

## 4. Isolated Hidden Future (STRICTLY PROHIBITED FROM RUNTIME INPUT)

> **WARNING**: The contents of this section must NOT be leaked into the agent's context during evaluation runs.

### ACTUAL_ROOT_CAUSE
1. **Unprotected Whole-File Atomic Overwrites**: `@deepseek-ai/dsh-storage-json` 使用内存 Map 维护权威状态，持久化采用 `writeAtomic`（写临时文件 + rename），仅有进程内 handle guard，完全没有跨进程文件锁，也没有 CAS / revision 检查。
2. **Non-Atomic Logical Creation**: `dsh-workspace.createCanonical` 分两次物理写：先 `table.put` 写入 workspaces 表，再 `setState` 写入 `global.workspaceIds`。
3. **Cross-Process Interleaving**: 启动脚本 `dsh_desktop_restart.ps1` 中的 `Wait-PortFree` 在超时后有 "proceeding anyway" 逻辑，导致新旧 DSH 实例并发运行。并发实例读取了不同的瞬时状态并相互覆盖，导致新记录被写入 table，但 global order 却被旧进程的快照覆写，造成孤立记录。

### POST_CUTOFF_DISCOVERIES
- 历史修复分支 `workspace-registry-phase1` 提交 `58d6569`。
- 测试用例 `dsh/workspace-registry-root-fix/test/storage-lost-update.test.mjs`。
- `scripts/aic/dsh_desktop_restart.ps1` 增加 `[switch]$FailClosed`，禁止 "proceed anyway"。
- `scripts/personal_ai_sync.py::workspace_registry_integrity()` 校验器。

### FINAL_VERIFIED_STATE
- 合并孤立 session 集合至 `a9d1651f`，删除孤立项 `c1e48f21`，消除重名路径，DSH 正常启动，全量测试通过。
