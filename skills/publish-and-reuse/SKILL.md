---
name: publish-and-reuse
description: 完整环境生命周期一体化编排：一键环境检查/体检、一键上传备份到 GitHub、一键增量更新（从 GitHub 到 DSH）、新设备环境复现与一键发布门禁。覆盖 Skills、DSH 插件、MCP 与 DSH 全局配置（AGENTS.md/settings）四层，agent 自动加载即可端到端闭环执行，无需用户逐步指导。
version: 1.1.0
triggers:
  - "一键更新环境 / 从 GitHub 同步 / 同步到 DSH / 刷新环境"
  - "一键上传环境 / 备份环境 / 同步到 GitHub / 导出环境配置"
  - "一键检查环境 / 环境体检 / 检查四层一致性"
  - "在新设备复现 / 安装我的环境"
  - "发布 Skill / 脚本 / 插件 / 整个仓库 / 跑发布门禁"
not_for:
  - "修改具体业务代码或单个 Skill 的逻辑实现（交给 minimal-implementation）"
  - "日常代码编写与单点 Bug 修复"
depends_on:
  - skill-repository-maintainer
  - dsh-config-sync
  - environment-bootstrap
---

# 发布与跨设备环境复现（publish-and-reuse）

## 定位

本 skill 是 **DSH 四层环境生命周期的最高层一体化编排器**。
**仓库是唯一事实源（SSOT）**，所有发布、备份、增量更新和环境复现都以此为基准。
当用户提出"上传环境"、"更新环境"、"从GitHub同步"、"同步到DSH"或"环境体检"时，Agent **无需用户分步指导，直接根据下述四大标准操作协议（SOP）自主端到端闭环执行**。

权威细节见 `docs/publishing.md`、`docs/sync-ongoing.md`、`docs/local-experience-and-cross-device-reuse.md`。

## 何时触发与适用边界

### 适用场景（何时触发）
- 用户要求"一键更新环境 / 从 GitHub 同步 / 同步到 DSH / 刷新环境"。
- 用户要求"一键上传环境 / 备份环境 / 同步到 GitHub / 导出环境配置"。
- 用户要求"一键检查环境 / 环境体检 / 检查四层一致性"。
- 用户要求"在新设备复现 / 安装我的环境"。
- 用户要求"发布 Skill / 脚本 / 插件 / 整个仓库 / 跑发布门禁"。

### 负向边界（不适用）
- 修改具体业务代码或单个 Skill 的逻辑实现（交给 `minimal-implementation`）。
- 日常代码编写与单点 Bug 修复。

---

## 四层事实源（SSOT）架构速查

| 层级 | 内容 | 仓库位置 | 运行时目标位置 | 作用与同步工具 |
|---|---|---|---|---|
| ① **Skill 包** | 16+ Skills | `skills/*` | `~/.dsh/skills/*` | `sync_skills.py --apply`（增量覆盖） |
| ② **DSH 插件** | 运行时 JS/MJS 插件 | `dsh/*` | `~/.dsh/profiles/web/plugins/*` | `sync_skills.py --plugins-destination ... --apply` |
| ③ **MCP 包** | `agent-switchboard` 等 | `mcp/*` | 仓库就地（git clone 路径） | git 就地运行，`cordis.patch.yml` 模板化引用 |
| ④ **DSH 全局配置** | `AGENTS.md` / `settings.yaml` / `profiles` / `presets` | `dsh-config/*` | `~/.dsh/*` | `sync_dsh_config.py`（export ↔ apply） |

---

## 协议一：一键环境体检（Check / Audit）

当用户要求"检查环境"、"环境体检"、"检查配置是否一致"时执行：

```powershell
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check
```

**执行与判定标准：**
1. 汇报四层检查结果：Skills、Plugins、dsh-config 归档、MCP 路径。
2. `dsh-config` 中 `extra` 出现 `.credentials.yaml` / `.anonymous-user-id` 等属于预期安全隔离，只要 `missing=0 different=0` 即可判定为通过。

---

## 协议二：一键更新环境（Update / Pull — 从 GitHub 同步到 DSH）

当用户要求"从 GitHub 同步"、"更新环境"、"同步到 DSH"、"拉取最新配置"时执行：

```powershell
# 1. 从远程拉取最新代码与配置归档
git pull

# 2. 一键同步 Skills 与 DSH 插件到运行时目录
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --profile full --apply

# 3. 恢复 DSH 全局配置（自动将 {{DESKTOP}}/{{DSH_HOME}} 等模板渲染为本机真实路径）
python skills\dsh-config-sync\scripts\sync_dsh_config.py apply `
  --display dsh-config --destination "$env:USERPROFILE\.dsh"

# 4. 执行一次聚合体检复核
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check
```

**交付与生效提醒：**
- 若更新了 DSH 插件或 `cordis.patch.yml`（如 MCP 配置），明确告知用户需重启 DSH 进程加载新代码。

---

## 协议三：一键上传/备份环境（Upload / Backup / Push — 同步到 GitHub）

当用户要求"上传环境"、"备份环境"、"把当前配置/技能推送到 GitHub"时执行：

```powershell
# 1. 导出本机 DSH 全局配置到仓库 dsh-config 骨架（自动执行凭据脱敏、设备路径模板化占位符替换）
python skills\dsh-config-sync\scripts\sync_dsh_config.py export `
  --source "$env:USERPROFILE\.dsh" --display dsh-config --template --with-optional

# 2. 执行仓库发布与安全门禁
python scripts\publish_all.py

# 3. 提交并推送
git add -A
git status --short
git commit -m "chore: backup environment (skills, plugins, mcp, dsh-config)"
git push
```

**安全拦截守则：**
- 敏感扫描必须为 `PASS`；如果检测到 `.credentials.yaml` 或内联明文密码，严禁提交推送并立即阻断。

---

## 协议四：新设备首次安装（Fresh Install / Restore）

在全新机器上快速复现整套 Agent 工具链：

```powershell
# 1. 安装 DSH，配置好 provider 与同名环境变量（BAI_API_KEY / CPA_API_KEY 等，密钥永不进仓库）
# 2. 克隆仓库
git clone <私有远程仓库URL> C:\Users\<user>\Desktop\skills
cd C:\Users\<user>\Desktop\skills

# 3. 源端自检
python scripts\validate_repo.py --strict
python scripts\run_skill_evals.py

# 4. 一键安装 Skills + DSH 插件
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --profile full --apply

# 5. 一键渲染恢复 DSH 配置
python skills\dsh-config-sync\scripts\sync_dsh_config.py apply `
  --display dsh-config --destination "$env:USERPROFILE\.dsh"

# 6. 聚合体检
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check

# 7. 启动/重启 DSH，开始会话验证
```

---

## 协议五：完整发布门禁（Publish All）

```bash
python scripts/publish_all.py
```

按顺序执行 7 道发布门禁：
1. `validate_repo.py --strict`（结构与注册表）
2. `quality_report.py --root . --strict`（Skill 行为质量）
3. `publish_check.py`（敏感扫描与可发布性）
4. `run_skill_evals.py`（结构性 evals）
5. `unittest tests`（仓库回归）
6. `unittest mcp tests`（MCP 回归）
7. `git diff --check`（空白错误）

---

## 安全纪律（铁律）

1. **凭据绝对不出包**：`.credentials.yaml`、`sessions/`、`storages/` 永远排除在归档和仓库之外。
2. **环境变量引用**：`settings.yaml` 只保留环境变量名引用（如 `apiKeyEnv: BAI_API_KEY`），换机配置同名系统环境变量即可。
3. **设备路径模板化**：`{{DSH_HOME}}`、`{{DESKTOP}}`、`{{HOME}}` 必须在 export 时正确替换，apply 时自动渲染。
4. **不破坏目标端无关文件**：所有 apply 仅做增量覆盖或更新，不清理未登记的目标端文件。

---

## 输出契约

每次执行后报告：
- **操作模式**：Check / Update (Pull) / Upload (Push) / Install / Publish
- **逐项命令与退出码**
- **四层状态矩阵汇总（Skills / Plugins / MCP / DSH Config）**
- **生效提示（是否需要重启 DSH）**
- **剩余风险或注意事项**
