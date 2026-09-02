# BOOTSTRAP — Personal AI 自主配置种子

本文件是这台电脑 Personal AI 生命周期的**唯一入口**。
用户只需要对这台电脑上的 Agent 说一句：

> **读取 https://github.com/ooooooooooooooooooop/agent-tools/blob/main/BOOTSTRAP.md，自主配置这台电脑**

安装 / 更新 / 恢复 / 同步的全部意图都已固化在本文件中。读到本文件的 Agent 必须自主执行到底，只在 §5 列出的情形下才向用户报告。

---

## 0. 读者与铁律（给 Agent）

- 读者 = 当前运行在这台电脑上的编码 Agent（DSH / Codex / Claude Code 等任意 Harness）。
- 用户不需要、也不应被要求知道：Skill 名、Git 命令、同步方向、aic、Memory 合并、Harness adapter。这些全部由本文件和编排器承载。
- 方向判定只用 git ancestry（fetch → ahead/behind → dirty）；**禁止** mtime / last-write-wins / timestamp 覆盖。
- **禁止**自动 `git add` / `commit` / `stash` / `reset` / `checkout` 覆盖 / `force push`。
- secrets 绝不进 git；只检测引用 `AVAILABLE / MISSING / NOT_REQUIRED`。
- device-local / derived（绝对路径、索引、sessions、缓存、node_modules）永不同步，每台设备本地重建。
- 所有步骤幂等：任何一步失败，修正原因后从 §1 重跑即可安全收敛，不会产生重复安装或数据丢失。

## 1. 判定：这台电脑处于哪种状态

依序执行只读检查：

1. 定位本机 agent-tools 克隆：依次探测 `<HOME>/Desktop/skills`、`<HOME>/Desktop/agent-tools`、`<HOME>/agent-tools`，要求存在 `.git` 且 `git remote get-url origin` 指向 `ooooooooooooooooooop/agent-tools`。命中后记为 `REPO`；都找不到则 `REPO = 缺失`。
2. 定位当前 Harness 的用户级 Skill 目录 `SKILLS_DIR`：
   - DSH：`<HOME>/.dsh/skills`（插件目录 `<HOME>/.dsh/profiles/web/plugins`）
   - Codex：`<HOME>/.codex/skills`
   - Claude Code：`<HOME>/.claude/skills`
   - 无法确定属于哪种 Harness 时按"未安装"处理（bootstrap 幂等，重复安装无害）。
3. **INSTALLED 判定**（三条全部成立）：
   - `REPO` 存在；
   - `<SKILLS_DIR>/publish-and-reuse/SKILL.md` 存在；
   - `python <REPO>/scripts/sync_skills.py --destination <SKILLS_DIR> --profile full --check` 退出码 0。

路由：

- INSTALLED → 直接走 §3（handoff `$publish-and-reuse` → AUTO_SYNC）。
- 任一条件不成立 → 走 §2（自主 bootstrap → 安装 full Skill profile → handoff `$publish-and-reuse` → RESTORE）。

## 2. BOOTSTRAP（没安装 Skill 的全新/残缺设备）

1. **Preflight**：`git --version` 与 `python --version` 可用；`git ls-remote https://github.com/ooooooooooooooooooop/agent-tools.git HEAD` 能连通。失败 → §5（环境/认证）。
2. **克隆 canonical**（public 仓库，无需认证）：
   `git clone https://github.com/ooooooooooooooooooop/agent-tools.git <HOME>/Desktop/skills`，记为 `REPO`。
   `REPO` 已存在则跳过克隆；存在但落后于远端则先按 §3 拉到最新再继续。
3. **校验 canonical**：`python <REPO>/scripts/validate_repo.py --strict` 必须退出码 0，否则 §5。
4. **安装 full Skill profile**（[skills.json](./skills.json) 中 profile `full`，含 publish-and-reuse）：
   `python <REPO>/scripts/sync_skills.py --destination <SKILLS_DIR> --profile full --apply`
   DSH 的 runtime/base/UI/overlay 不由此工具复制；统一由随后执行的
   `DSH_HOME=<HOME>/.dsh python <REPO>/scripts/aic/aic.py apply dsh` 接管。
   该工具只做增量覆盖，绝不删除目标端额外文件；装完重跑 `--check` 必须退出码 0。
5. **Handoff 给 `$publish-and-reuse`**：打开 [`skills/publish-and-reuse/SKILL.md`](./skills/publish-and-reuse/SKILL.md)（权威细节：[`references/personal-ai-lifecycle-sync.md`](./skills/publish-and-reuse/references/personal-ai-lifecycle-sync.md)），严格按其 **RESTORE** 模式契约执行：
   `python <REPO>/scripts/personal_ai_sync.py restore`
   restore 自动完成：克隆 personal-ai-state（private，需本机 Git SSH 认证）→ aic discover → 恢复 Skills → `aic apply dsh` 重建固定 Node/Base/UI/五个 overlay/Profile → 校验 Memory canonical 可读 → **校验 DSH 会话历史**（备份计数 / live 计数 / 已知锚点 / schema 探针；非 `PASS|NOT_APPLICABLE` 时总体不得 PASS，历史缺失绝不因"配置恢复成功"而放行）→ 检测 secret 引用 → 已安装 Harness drift 检查。全部幂等。
6. restore 结果 `PASS` → §4 收尾；`REVIEW` / `BLOCKED` → §5。

## 3. AUTO_SYNC（已安装设备的日常入口）

1. **Handoff 给 `$publish-and-reuse`**（本机已安装；权威契约同 §2 第 5 步的 reference），执行：
   `python <REPO>/scripts/personal_ai_sync.py sync`
2. 方向由编排器 [`scripts/personal_ai_sync.py`](./scripts/personal_ai_sync.py) 自动判定，Agent 不需要也**不许**替它选方向：

   | 分类（git ancestry） | 自动动作 |
   |---|---|
   | IN_SYNC | NO ACTION |
   | REMOTE_AHEAD + clean | fast-forward PULL → 受影响面 refresh |
   | LOCAL_AHEAD + 验证通过 + privacy scan PASS | PUSH |
   | personal-ai-state 仅 memory 路径不相交 | 确定性 MERGE（MemoryProvider 冻结契约） |
   | dirty / diverged curated / 隐私命中 | 不碰，转 §5 REVIEW |

   随后自动完成受影响 Harness runtime refresh 与派生索引重建，并写 machine-local checkpoint。
3. 结果 `PASS` → §4 收尾；`REVIEW` / `BLOCKED` → §5。

## 4. 收尾（不需要用户做任何事）

逐字使用以下短输出模板，不解释 Skill 名、Git 细节、方向判断过程；用户说"展开"时才下钻：

```text
Personal AI Bootstrap

mode                 RESTORE / AUTO_SYNC
agent-tools          <状态>
personal-ai-state    <状态>
runtime              <状态>
dsh-session-history  <状态>（backup=<n> live=<n> missing=<n>）
secrets              <状态>

Result: PASS
```

`dsh-session-history` 状态 = `PASS`（备份与 live 会话数匹配、锚点与 schema 探针通过）/ `NOT_APPLICABLE`（从未配置备份的 fresh 设备，不阻塞）/ `PARTIAL` / `FAIL`。**不得用一个总体 PASS 混合各 plane**；历史缺失时总体 `Result` 一律降为 REVIEW。


## 4.1 Harness compatibility overlay（自动恢复）

DSH 的固定 Base、Node、UI bundle、五个 overlay、Profile patch、组合 manifest
和原子回滚统一由：

    DSH_HOME=<HOME>/.dsh python <REPO>/scripts/aic/aic.py apply dsh

负责。不得再直接调用旧的测试安装器；旧安装器仅作为历史取证材料保留。
## 5. 只有这些情况才报告用户

其余一切问题（含拉取、合并、安装、refresh、重跑）必须自主解决，禁止抛回用户、禁止把门禁输出当交付物。

| 情形 | 信号 | 向用户报告什么 |
|---|---|---|
| 认证 / 环境 | git 或 SSH 认证失败、private 仓库不可达、`BLOCKED_AUTH`、git/python 缺失 | 需要在这台电脑上配置什么访问权限或安装什么 |
| secret 缺失 | sync/restore 报告 secrets `MISSING` 且阻塞当前 active route | 缺哪个**环境变量名**（绝不包含值） |
| dirty / diverged canonical | `LOCAL_DIRTY` / `DIVERGED` / `CONFLICT` / `UNTOUCHED` | 哪个仓库、哪些路径、为什么不自动碰 |
| 隐私 / 冲突 | `BLOCKED_PRIVACY`、curated state 双端修改、record 不可变元数据冲突 | 命中位置与建议的人工处理方式 |

已知外部 blocker 状态未变时只在输出中带一句 `known external blocker, unchanged`，不展开、不重复建议。

## 6. 验收（Agent 自查，全部通过才算完成）

- `python <REPO>/scripts/validate_repo.py --strict` 退出码 0；
- `python <REPO>/scripts/aic/aic.py validate` = VALID；
- 每个已安装 Harness 的 `aic diff` = NO DRIFT（未安装 = OPTIONAL_NOT_INSTALLED，不自动装齐）；
- `python <REPO>/scripts/personal_ai_sync.py check` 结果 `PASS`（各 plane `IN_SYNC`）；
- restore 的 `dsh-session-history` step = `PASS | NOT_APPLICABLE`（含已知锚点 `session-869904c0-fcd0-4ea3-a3b7-fec230ac8017` 的 backup/live 存在性）；
- fresh 恢复后跑一个低风险普通任务，确认 Personalization context 正常（不得以旧聊天 history 作为恢复依据）；恢复过历史的设备须确认旧会话在运行时可见（会话文件挂载 + schema 探针），不得只看左侧列表。
