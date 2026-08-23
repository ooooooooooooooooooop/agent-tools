# 本地特有经验优化总结与跨设备复用方案

> 生成时间：2026-08-22。依据：`~/.dsh` 配置层只读审计 + `<repo-root>` 仓库只读审计 + 3 路联网窄探针（CPA 网关）+ execution-discipline skill 内嵌的既有业界验证表。
> 全程只读审计，未修改任何配置；凭据值未出包（仅保留环境变量名引用）。

---

## 0. 结论速览

1. 本机沉淀了 **4 层、20+ 项可复用独有经验**：治理规则（AGENTS.md 8 模块）→ DSH 插件（3 个自研）→ 架构模式（两平面治理）→ 技能栈（13/14 独有）→ 证据链工程（6 个审计脚本 + 注册表校验）。
2. 复用方案 = **三平面同步**（配置骨架 / 技能栈 / 源码仓库）+ 凭据隔离 + SHA-256 校验 + check→apply→check，工具链已齐备（dsh-config-sync、environment-bootstrap、sync_skills.py）。
3. 业界对照结论：本地方案与 dotfiles 生态、AI 助手配置 SSOT、多智能体治理模式**高度吻合**，且有 2 个差异化领先点（fork 上下文压缩插件、证据驱动的审计→规则闭环）。当前最大缺口是**绝对路径耦合**与**文档数字滞后**，需先修补再跨设备。

---

## 1. 本地特有经验优化全景

### 1.1 治理层：`~/.dsh/AGENTS.md`（112 行，全局 8 大模块）

| 模块 | 核心经验 | 独特价值 |
|---|---|---|
| 一 | 端到端自主闭环：严禁把生命周期/手动 handoff 负担抛给用户 | 长任务全由 Agent 后台推进，用户零操作 |
| 二 | 跨模型长程监督：零轮询，统一单次事件/结果长轮询（`wait_supervisor_event`/`job_output(wait)`/`wait_task_receipt`/`request_result`） | 消除高频空转查询（审计曾发现 288 次无 wait 查询） |
| 三 | 子代理委派与上下文隔离：Read-as-Execution、模式判定即熔断、禁全量 Fork、物理验收门禁（命令+Exit Code+日志证据） | 主会话保持纯决策状态，杜绝"口头完成" |
| 四 | 会话生命周期：Goal/Todo/Work Memory 三位一体 + 后台静默沉淀 + 自主紧凑化 | 长程断点可恢复，跨会话黑板共享 |
| 五 | Prompt Cache 前缀稳定性 + 模型分级：静态根区/动态槽位分层；执行端默认 `gpt-5.6-luna-max` / `gemini-3.7-flash-high` 工作马 | KV Cache 命中率 >95% 目标；成本与能力解耦 |
| 六 | 长程任务断点与批量执行：>50 步/30 分钟设 checkpoint；审计单次建索引 + 切片委派；离线走 Batch | 失败从最近断点恢复，不整段重放 |
| 七 | 外部研究两段式物理隔离：异步队列 + 单次 `request_result` 接管；极窄探针（≤100 字）；防惊群；共识直接收敛 | 阻断 MCP 超时（-32001）与并发风暴 |
| 八 | 执行纪律防再犯：五条铁律（等通知不轮询/门禁是诊断信号/先探测通道再窄探针/探查派子代理/派发带进度回报协议） | 由一次 13 小时真实长程会话审计固化 |

### 1.2 插件层：3 个自研 DSH 插件（`profiles/web/plugins/`）

| 插件 | 解决什么 | 量化效果 |
|---|---|---|
| `dsh-subagent-context-summary.js` | fork 子代理父上下文 >30k 字符时，只注入 compaction 摘要 + 最近 1 轮完整对话 | 典型省子代理侧 ~50–70% 输入 token |
| `llm-retry-claude-code.js` | 将普通模型请求 retry 抬升到 Claude Code 风格基线（max 10 次 / 初始 500ms / 单次上限 8s / 25% jitter），不覆盖已有更强/always 策略 | 弱策略自动补齐，故障自愈 |
| `llm-overflow-classifier.js` | 把 provider"输入超限"错误统一重分类为 `CONTEXT_WINDOW_EXCEEDED` | 触发正确的 compaction/retry 链，不再当普通失败误处理 |

配套架构经验：**插件优于 node_modules 补丁**（`cordis.patch.yml` 固化，npm update 后仍保留）；**冻结输入对象不突变**（包装时构造新对象）。

### 1.3 架构层：两平面治理（host vs preset）

- `profiles/web/cordis.patch.yml`：把会发布进程唯一资源的 Cordis/MCP rows 注入 **process-global host 层**，避免 per-preset provider/serverName 冲突导致新会话创建失败。
- `.agent-presets/cc/agent.cordis.yml`：**用户级复制版 preset**（标准 coding agent + 运行时读写 + Cordis composition 指导），绝不改部署自带 shipped preset（升级会覆盖）。
- cc preset 随附 2 个部署专用 skill：`cordis-plugin-development`、`editing-cordis-compositions`。

### 1.4 技能栈层：14 个 Skill（13 个评为独有经验）

| 职能 | Skill | 核心价值 |
|---|---|---|
| 执行纪律 | `execution-discipline` | 五条铁律（零轮询/门禁自修/窄探针/委派探查/进度回报）+ 熔断补充 |
| 决策防偏 | `decision-gates` | 证据锚/对抗审计/一致性/成本/摘要偏差五道闸门 |
| 规模路由 | `task-mode-router` | 按规模与风险选最轻但足够的执行深度 |
| 修改前澄清 | `clarify-before-change` | 改前界定目标/范围/风险/验收，可逆假设继续 |
| 最小实现 | `minimal-implementation` | 最小正确改动 + 可复核证据 |
| 跨设备复用 | `dsh-config-sync` | DSH 配置脱敏打包/恢复，SHA-256，强制排除凭据运行态 |
| 跨设备复用 | `environment-bootstrap` | 从源仓库恢复已登记 Skill，只读审计/显式应用 |
| 仓库维护 | `skill-repository-maintainer` | 注册表/包边界/发布审计 + check→apply→check 同步 |
| 质量门禁 | `skill-quality-gate` | Skill 触发/流程/输出/回归行为质量只读评估 |
| 复杂任务 | `unified-taskflow` | anchor/checkpoint/design/恢复/验收追踪 |
| 写作 | `natural-rewrite` | 保持事实语气仅改善表达 |
| 办公 | `weekly-work-summary` | 上海时区 + 中国实际工作日，固定周报契约 |
| 决策模拟 | `simulate-elite-experts` | 真实人物+领域专家+全知视角四视角 |
| 运维 | `agent-switchboard-ops` | 受管 supervisor/跨模型委派/验收纪律 |

### 1.5 工程化证据链层（skills 仓库）

- **6 个审计脚本**（`dsh-*.js`）构成"真实会话审计 → 规则/插件优化"的证据闭环：
  - `dsh-event-time-audit.js`：按**事件时间**切分策略前后，避免把整个会话粗略归因；
  - `dsh-postpolicy-audit.js` / `dsh-postpolicy-deep.js`：会话级回归画像 + 下钻到 wait 字段，直接揭示无 wait 轮询；
  - `dsh-retry-analysis.js` / `dsh-verify-cpa.js`：按 **provider × failure code × 消息簇** 定位重试根因；
  - `dsh-token-summary.js`：模型/项目/任务类型/缓存/高重试 session 多维成本画像。
- **注册表 + 校验器**：`skills.json`/`mcp.json` ↔ `validate_repo.py --strict`、`quality_report.py --strict`。
- **同步脚本** `sync_skills.py`：SHA-256 只读差异 → 显式 apply → 再 check，永不删除目标端额外文件。
- **mcp/agent-switchboard**：跨模型委派桥（SQLite/CLI/IDE bridge，连接 Codex/Claude/Gemini/Antigravity），事件驱动 + 廉价执行者与权威决策者分离。

---

## 2. 跨设备复用方案

### 2.1 三平面同步架构

| 平面 | 内容 | 工具 | 凭据/运行态处理 |
|---|---|---|---|
| ① 配置骨架 | `AGENTS.md` + `settings.yaml` + `profiles/` 插件源码 + `.agent-presets/` | `dsh-config-sync` | 强制排除 `.credentials.yaml`/`sessions`/`storages`；settings 只带 `apiKeyEnv` 环境变量名 |
| ② 技能栈 | `~/.dsh/skills/*` | `environment-bootstrap` / `sync_skills.py` | 纯源码，无敏感项 |
| ③ 源码仓库 | 整个 skills 仓库（git SSOT） | `git clone` + `validate_repo.py --strict` | 排除 `dsh-token-result.json` 等生成物/运行态 |

### 2.2 新设备落地 SOP（7 步）

1. 安装 DSH，配置 provider（CPA/BAI 等）与同名环境变量（`BAI_API_KEY`/`CPA_API_KEY` 等，密钥永不进包）。
2. `git clone` skills 仓库到目标设备。
3. 源端自检：`python scripts/validate_repo.py --strict` + `quality_report.py --strict`。
4. 技能栈同步（只读差异）：`python scripts/sync_skills.py --destination <目标 ~/.dsh/skills> --check`，确认 missing/different/extra。
5. 配置骨架同步：A 机 `dsh-config-sync export`（含 SHA-256 manifest + 敏感扫描 PASS）→ 拷贝归档到 B 机 → `apply` → 同一目标端再 `check` 核对 SHA-256。
6. 修补绝对路径耦合（见 2.3）。
7. 冒烟验证：跑一个真实会话，用审计脚本重建基线并对比重试率/token/缓存命中。

### 2.3 当前不可直接移植的缺口（先修补再复用）

| 缺口 | 位置 | 修法 |
|---|---|---|
| 绝对路径耦合 | `profiles/web/cordis.patch.yml`、`weekly-work-summary`（`C:\Desktop\日报`/`C:\Desktop\共享`）、`agent.cordis.yml` | 参数化为 `$DSH_HOME`/`$HOME` 模板，参考 chezmoi 模板渲染 |
| 文档数字滞后 | 仓库 `AGENTS.md` 写"9 个"实为 13 个；`mcp.json` commit（9d351…/1.0.32）与 README（821ef…/1.0.30）不一致 | 顺手修正，provenance 单独复核 |
| 运行态数据 | 会话审计基线（sessions/ JSONL）各设备独立 | 审计脚本设计为每设备重建基线，不做跨设备迁移 |

---

## 3. 业界做法对照（2026-08-22 联网探针）

### 3.1 dotfiles 生态 → 本方案定位

| 方案 | 优势 | 劣势 | 本方案对照 |
|---|---|---|---|
| chezmoi | 模板渲染、跨平台差异化、密码管理器集成 | 学习成本高、改变编辑习惯 | **建议借鉴**：模板渲染可解决绝对路径缺口 |
| GNU Stow | 轻量纯软链、零门槛 | 无模板/密钥/差异化 | 本方案比它强（有 SHA-256 + 敏感扫描） |
| dotbot | YAML 引导、简单 | 静态分发、无模板加密 | 本方案最接近"dotbot 增强版" |
| Nix home-manager | 声明式、可复现 | 学习曲线极陡、生态绑定 | 对本场景过重 |

> 结论：本方案 ≈ **dotbot 增强版（+SHA-256 校验 + 敏感扫描）**；最值得补的是 chezmoi 式**模板渲染**（解决 2.3 绝对路径耦合）。

### 3.2 AI 助手配置跨设备复用（业界 7 条）→ 本地覆盖度

| 业界做法 | 本地状态 |
|---|---|
| ① 仓库级规则版本化管理（AGENTS.md/CLAUDE.md 入 git） | ✅ 已有（skills 仓库 + AGENTS.md） |
| ② 全局配置 Dotfiles 同步 | ✅ 已有（dsh-config-sync + sync_skills.py） |
| ③ MCP 标准分发 Skills | 🟡 部分（skill 包分发已有，未封装为 MCP 服务） |
| ④ 多端配置单源维护 SSOT | 🟡 部分（skills.json 是 SSOT，未派生到 Cursor/Windsurf 等专有路径） |
| ⑤ CI 静态校验 + 回归 | ✅ 静态校验已有；❌ 缺 headless agent evals 自动回归 |
| ⑥ 动态上下文编译防膨胀 | ✅ 本地更激进：fork 上下文压缩插件（业界用 repomix） |
| ⑦ 规则与环境凭据解耦 | ✅ 已有（`.credentials.yaml` + `apiKeyEnv` 引用设计） |

### 3.3 多智能体治理模式 → 本地对应物

| 模式 | 业界实现 | 本地对应 |
|---|---|---|
| Mailbox 事件驱动 | CrewAI Flows / LangGraph Send-Command / Anthropic Agent Teams | DSH 主动完成通知 + 单次 wait 长轮询 |
| Circuit Breaker 熔断 | Resilience4j/Polly/pybreaker（框架多需外置） | execution-discipline"连续失败 3 次即熔断" |
| 重试与退避 | LangGraph RetryPolicy / CrewAI task 层 | `llm-retry-claude-code.js` 插件（10 次/500ms/8s/jitter 25%） |
| 上下文压缩/Checkpoint | LangGraph Checkpointer + durable execution | Goal/Todo/Work Memory 三位一体 + fork 摘要插件 |
| 结构化记忆 | LangGraph Store / CrewAI Memory / Mem0·Zep | `.agent-broker/topics/*/work_memory.md` 黑板 |

### 3.4 既有已验证业界对照（execution-discipline 内嵌，2026-08-22 已 web 核验）

- Anthropic Agent Teams mailbox（零轮询）：https://code.claude.com/docs/en/agent-teams
- LangGraph RetryPolicy / Durable Execution：https://docs.langchain.com/oss/python/langgraph/use-graph-api#add-retry-policies 、https://docs.langchain.com/oss/python/langgraph/durable-execution
- Azure Circuit Breaker 模式：https://learn.microsoft.com/azure/architecture/patterns/circuit-breaker
- 上下文/记忆：https://code.claude.com/docs/en/how-claude-code-works 、https://langchain-ai.github.io/langgraph/concepts/persistence/ 、https://docs.mem0.ai/migration/platform-v2-to-v3 、https://help.getzep.com/v2/memory

---

## 4. 业界复用全景：这种内容到底怎么被复用（2026-08-22 深度探针）

业界复用 Agent 规则/技能，本质是一条 **四层流水线**：先有可移植格式，再走分发渠道，个人层解决跨设备，团队层解决规模化治理。

### 4.1 格式标准层（内容长什么样才"可复用"）

| 格式 | 定位 | 互操作性 | 采用度 |
|---|---|---|---|
| **Anthropic Agent Skills（SKILL.md frontmatter）** | 模块化技能/工具扩展标准：YAML Frontmatter 声明元数据 + Markdown 执行指令 | 与 Claude/MCP 生态深度绑定，通用结构易被其他框架解析，跨平台需兼容层 | 事实上的技能定义范式之一 |
| **AGENTS.md 开放倡议** | 跨平台通用行为准则/项目级指引（类似 robots.txt），统一声明仓库边界与执行规则 | 厂商中立、纯 Markdown，各类 Agent CLI/IDE/SDK 统一读取 | 开源社区与多方厂商倡议/试点阶段 |
| **OpenContext** | 上下文提取/封装/动态注入协议，侧重工程环境与运行时信息标准化分发 | Schema/API 解耦，可桥接 LSP/MCP 与多 Agent 运行时 | 早期集成阶段 |
| **.cursor/rules** | Cursor 仓库级代码规范 + Glob 路径触发的文件级规则 | 语法简单但偏专有，跨 IDE 需转换映射 | AI 编程领域普及率极高，事实标配 |

> 关键结论：**没有统一标准，但"Markdown 指令 + 结构化元数据 + Git 托管"是共同底座**。本仓库 `SKILL.md + agents/openai.yaml + skills.json` 的结构正好落在 Anthropic Agent Skills 与 AGENTS.md 倡议的交集上。

### 4.2 分发生态层（内容怎么到达别的设备/工具）

| 通道 | 现状 | 适用 |
|---|---|---|
| **官方/社区注册表与市场**（Claude Skills marketplace、Smithery、Glama） | 随 MCP 崛起成为"一键发现/托管分发"核心枢纽 | 面向终端用户开箱即用 |
| **npm / pip / git 子模块** | 底层事实标准：运行时依赖打包、版本锁定、二次开发集成 | 面向开发者，与既有包管理体系无缝 |
| **个人跨设备同步**（chezmoi / bare repo / 云盘） | 中心化方案受限时的轻量补充：私有 Prompt、定制脚本、密钥环境 | 重度个人用户去中心化跨端 |

> 本地方案现状：自建注册表（skills.json/mcp.json）+ 自研同步脚本（sync_skills.py/dsh-config-sync）+ git SSOT，相当于"自托管 npm/pip + 个人 chezmoi 路线"，**缺的是对外市场通道**（未发布到 Smithery/Glama 等）。

### 4.3 个人跨设备层（业界主流工具链）

- **chezmoi**：模板渲染 + 跨平台差异化 + 密码管理器集成，管理 dotfiles/全局 Prompt/工具配置；学习成本高但功能最全。
- **GNU Stow / bare git repo**：纯软链或 `git --bare` + alias 管理，轻量零门槛，无模板/加密。
- **云盘/iCloud/OneDrive 同步**：零配置但无版本/无校验/无差异审查，不适合规则类内容。
- **包管理器路线**（npm/pip/git submodule）：把 skill 当依赖装到目标端，版本可锁定、可回滚。

> 本地方案 = 自研"校验强化的 Stow"（SHA-256 + 敏感扫描 + check→apply→check），比纯软链安全，比 chezmoi 缺模板渲染（正好对应 2.3 的绝对路径缺口）。

### 4.4 团队/企业治理层（规模化复用的业界标配）

1. **模板仓库 + Git 模板**：统一 Agent 配置/规则/Skills/MCP 脚手架，用 Git template、Copier/Yeoman 或内部 CLI 一键初始化。
2. **顶层 `CLAUDE.md`/`AGENTS.md`**：组织级通用规范，目录级允许覆盖，声明上下文加载优先级、代码风格、测试与提交要求。
3. **分层配置与版本治理**："组织基线 → 团队模板 → 仓库规则 → 子目录规则"继承模型，全部入 Git 评审/变更记录/责任人。
4. **Hooks 强制执行**：pre-commit/pre-push/post-edit + 命令白名单，自动触发格式化、敏感扫描、测试、审批、禁危险操作。
5. **CI 校验与门禁**：校验规则完整性、技能元数据、依赖与权限、提示词注入风险、许可证合规、单测、变更影响，未过不得合并。
6. **Evals 与持续回归**：为核心 Agent/Skill 建立标准任务集，按成功率/正确性/成本/延迟/安全指标，随 PR、版本、模型升级持续跑。
7. **沙箱、最小权限与人工审批**：容器/VM 沙箱、只读文件系统、网络白名单、短期凭证；高风险写操作/生产访问/数据导出必须人工确认。
8. **安全审查与运营反馈闭环**：上线前威胁建模/红队/隐私与许可证审查/供应链扫描；上线后审计日志、事故复盘、反馈、定期淘汰升级。

> 本地方案覆盖度：**1（模板 `_template/` ✅）、2（AGENTS.md ✅）、3（git SSOT + validate ✅）、5（validate_repo.py/quality_report.py ✅）、7（凭据解耦 + 沙箱 ✅）**；缺口：4（hooks 强制）、6（headless evals 自动回归）、8（上线前红队/供应链审查、运营反馈闭环）。

### 4.5 一句话总览

> **业界复用 = 标准格式（Markdown+元数据）→ 多渠道分发（市场/包管理/git）→ 个人工具链（chezmoi 系）→ 团队治理（模板+hooks+CI+evals+安全审查）**。本地方案在"格式、git 托管、自研校验同步"上与国际接轨，最值得补的三件事：**模板渲染（解决绝对路径）、hooks/CI evals 回归、上市场分发（Smithery/Glama 类）**。

---

## 5. 下一步建议

- **P0**：本报告 + 复用 SOP 固化为可执行产物（一键 `export`/`restore` 脚本或独立 skill，如 `portable-device-reuse`）。
- **P1**：修补 2.3 缺口——`cordis.patch.yml`/周报路径模板化、AGENTS.md 与 mcp.json 数字/版本修正。
- **P2**：CI 增加 headless agent evals 回归（对应业界做法⑤），防规则漂移。
- **P3**：把 skill 包同步升级为 MCP 标准分发（对应业界做法③），实现跨工具（Claude Code/Cursor）统一挂载。

---

## 6. 改造落地记录（2026-08-22，P0/P1 第一批已实施）

| 改善 | 落地物 | 状态 |
|---|---|---|
| ① 模板渲染 | `skills/dsh-config-sync/scripts/sync_dsh_config.py`：新增 `--template`（`{{DSH_HOME}}`/`{{DESKTOP}}`/`{{HOME}}` 占位符导出/恢复）+ `--with-optional`（导出 profiles/.agent-presets/patches）；`SKILL.md` 新增"模板渲染"章节 | ✅ 已实现，往返冒烟通过（设备路径→占位符→目标设备路径，post-apply PASS） |
| ② CI + hooks + evals | `.githooks/pre-commit`（四门禁本地钩子，`git config core.hooksPath .githooks` 安装）；`scripts/run_skill_evals.py`（结构 evals 13/13 PASS + 可选 --live）；`ci.yml` 追加 evals + hook 安装验证步骤 | ✅ 已实现，本机验证通过（bash 钩子由 CI ubuntu 验证） |
| ③ 发布就绪 | `docs/publishing.md`（可发布性矩阵：skills 可发布、agent-switchboard 因 PolyForm Noncommercial 暂缓、DSH 插件走 git 分发）；`scripts/publish_check.py`（设备路径/许可证/文件齐全扫描） | ✅ 已实现，PASS（含 2 处误报修复：许可证标识归一化、tests/ 目录豁免） |

**验证证据**：`validate_repo.py --strict` PASS（13 skill + 1 MCP，0 错 0 警）；`quality_report.py --strict` PASS（13/13）；`run_skill_evals.py` 13/13 PASS；`publish_check.py` PASS；`python -m unittest discover -s tests` 10 tests OK；`git diff --check` 干净。

**遗留（P1/P2 后续）**：`weekly-work-summary` 按用户裁定**不发布、不模板化**（个人办公专用，设备绑定），已在 `publish_check.py` 的 `PUBLISH_EXCLUDED` 中豁免并补充 `.mjs/.cjs` 扫描后缀；CI 尚未接 `publish_check.py`；`mcp.json` 与 MCP README 的 upstream 版本不一致待复核；headless evals 的 `--live` 模式待接真实模型通道。

---

## 附：审计边界与证据

- 审计只读；`.credentials.yaml` 仅确认存在与顶层 key（`version`/`refs`），值未读取未输出。
- 凭据环境变量名（供目标设备设置，不属泄密）：`OPENCODE_GO_API_KEY`、`DEEPSEEK_API_KEY`、`KIMI_CODING_API_KEY`、`BAI_API_KEY`、`ANY_API_KEY`、`CPA_API_KEY`。
- 敏感路径：`~/.dsh/.credentials.yaml`、`sessions/`、`storages/`、`attachments/`；含设备绝对路径的 `profiles/web/cordis.patch.yml`、`agent.cordis.yml`。
- 子代理证据：`Get-Location`/`Test-Path` 探针通过；读操作命令摘要已回传；两子代理均未写文件。
