---
name: publish-and-reuse
description: 完整环境生命周期一体化编排：一键上传环境（含 Skills、插件、MCP 与 DSH 配置整体打包上传 GitHub）、一键更新环境（从 GitHub 同步到 DSH）、一键四层体检对比差异与新设备环境复现。上传与更新前强制执行四层体检对比差异，agent 自动加载即可端到端闭环执行，无需用户逐步指导。
version: 1.2.0
triggers:
  - "一键上传环境 / 上传环境 / 备份环境 / 同步到 GitHub / 推送环境配置"
  - "一键更新环境 / 从 GitHub 同步 / 同步到 DSH / 刷新环境 / 拉取环境"
  - "一键检查环境 / 环境体检 / 检查四层一致性 / 对比环境差异"
  - "在新设备复现 / 安装我的环境"
  - "发布 Skill / 脚本 / 插件 / 整个仓库 / 跑发布门禁"
not_for:
  - "在非 skills/agent-tools 仓库的其他业务工作区中执行环境同步、更新或上传（本 skill 仅在 agent-tools 仓库本体生效）"
  - "修改具体业务代码或单个 Skill 的内部逻辑实现（交给 minimal-implementation）"
  - "日常代码编写与单点 Bug 修复"
depends_on:
  - skill-repository-maintainer
  - dsh-config-sync
  - environment-bootstrap
---

# 发布与跨设备环境复现（publish-and-reuse）

## 定位

本 skill 是 **DSH 四层环境生命周期的最高层一体化编排器**。
**仓库是唯一事实源（SSOT）**，所有上传、更新、体检和环境复现都以此为基准。
核心原则：
1. **一键上传**：一体化覆盖四层（Skills、DSH 插件、MCP、DSH 全局配置），自动脱敏打包并推送到 GitHub。
2. **一键更新**：从 GitHub 拉取并全量应用到本机 DSH 运行时。
3. **体检先行**：**上传与更新之前，强制先执行四层体检对比差异**，明确改动面并完成安全拦截。

权威细节见 `docs/publishing.md`、`docs/sync-ongoing.md`、`docs/local-experience-and-cross-device-reuse.md`。

## 何时触发与适用边界

### 适用场景（何时触发）
- 用户要求"一键上传环境 / 上传环境 / 备份环境 / 同步到 GitHub / 推送环境配置"。
- 用户要求"一键更新环境 / 从 GitHub 同步 / 同步到 DSH / 刷新环境 / 拉取环境"。
- 用户要求"一键检查环境 / 环境体检 / 检查四层一致性 / 对比环境差异"。
- 用户要求"在新设备复现 / 安装我的环境"。
- 用户要求"发布 Skill / 脚本 / 插件 / 整个仓库 / 跑发布门禁"。

### 负向边界（不适用）
- **非 agent-tools 仓库目录**：在其他普通业务项目或工作区中，禁止使用本 skill 随意同步、拉取或覆盖环境（本 skill 仅限在本项目/仓库本体生效）。
- 修改具体业务代码或单个 Skill 的逻辑实现（交给 `minimal-implementation`）。
- 日常代码编写与单点 Bug 修复。

---

## 四层事实源（SSOT）架构速查

| 层级 | 包含内容 | 仓库事实源位置 | 运行时目标位置 | 作用与同步机制 |
|---|---|---|---|---|
| ① **Skill 包** | 16+ Skills 全部技能栈 | `skills/*` | `~/.dsh/skills/*` | `sync_skills.py` 增量同步与校验 |
| ② **DSH 插件** | 5 个用户级 JS/MJS 守护插件 | `dsh/*` | `~/.dsh/profiles/web/plugins/*` | `sync_skills.py --plugins-destination ...` 自动同步 |
| ③ **MCP 包** | `agent-switchboard` 跨模型网桥 | `mcp/*` | 仓库就地（git 路径） | git 就地运行，`cordis.patch.yml` 模板化引用与校验 |
| ④ **DSH 全局配置** | `AGENTS.md` / `settings.yaml` / `profiles` / `presets` | `dsh-config/*` | `~/.dsh/*` | `sync_dsh_config.py` 模板化脱敏导出与渲染恢复 |

---

## 协议一：一键环境体检（Check / Audit — 独立体检）

当用户要求"检查环境"、"环境体检"、"对比差异"时执行：

```powershell
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check
```

**判定标准：**
- Skills (18 项)：`missing=0 different=0`（PASS）
- Plugins (5 项)：`missing=0 different=0`（PASS）
- DSH Config：`missing=0 different=0`（`extra` 中的 `.credentials.yaml` / `.anonymous-user-id` 属预期本地隔离）
- MCP：`issues=none`（引用的脚本与 cwd 路径均有效）

---

## 协议二：一键更新环境（Update / Pull — 体检 ➔ 拉取 ➔ 应用 ➔ 复核）

当用户要求"从 GitHub 同步"、"更新环境"、"同步到 DSH"时，**严格按以下 4 步执行**：

```powershell
# 步骤 1【更新前体检】：对比当前本地与已安装的基线差异
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check

# 步骤 2【拉取远程最新代码与配置归档】：
git pull

# 步骤 3【应用更新到 DSH 运行时】：
# 3.1 同步 Skills 与 DSH 插件
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --profile full --apply

# 3.2 恢复 DSH 全局配置（自动将 {{DESKTOP}}/{{DSH_HOME}} 等模板渲染为本机真实路径）
python skills\dsh-config-sync\scripts\sync_dsh_config.py apply `
  --display dsh-config --destination "$env:USERPROFILE\.dsh"

# 步骤 4【更新后复核】：再次体检确认差异归零
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check
```

**生效提醒：** 若更新包含 DSH 插件或 `cordis.patch.yml`（MCP 配置），提示用户需重启 DSH 进程加载新代码。

---

## 协议三：一键上传环境（Upload — 导出 ➔ 门禁体检 ➔ 推送 GitHub）

一键将 **Skills、DSH 插件、MCP 源码与 DSH 全局配置四层** 完整打包上传到 GitHub：

```powershell
# 步骤 1【配置脱敏导出】：将本机 ~/.dsh 导出至仓库 dsh-config 骨架（自动路径模板化占位符替换）
python skills\dsh-config-sync\scripts\sync_dsh_config.py export `
  --source "$env:USERPROFILE\.dsh" --display dsh-config --template --with-optional

# 步骤 2【上传前体检与门禁】：执行全量四层体检与发布安全门禁（拦截密钥与格式错误）
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check

python scripts\publish_all.py

# 步骤 3【提交并推送到 GitHub】：
git add -A
git status --short
git commit -m "chore: upload environment (skills, plugins, mcp, dsh-config)"
git push
```

**安全拦截守则：**
- 敏感扫描与发布门禁必须全绿；若检测到 `.credentials.yaml`、明文密码或语法错误，**严禁提交并立即阻断**。

---

## 协议四：新设备首次安装（Fresh Install / Restore）

在全新机器上一键复现整套环境：

```powershell
# 1. 安装 DSH，配置 provider 与同名环境变量（BAI_API_KEY / CPA_API_KEY 等）
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

# 5. 一键渲染恢复 DSH 全局配置
python skills\dsh-config-sync\scripts\sync_dsh_config.py apply `
  --display dsh-config --destination "$env:USERPROFILE\.dsh"

# 6. 一键全量体检验证
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check

# 7. 启动 DSH，验证工具与配置生效
```

---

## 安全纪律（铁律）

1. **凭据绝对不出包**：`.credentials.yaml`、`sessions/`、`storages/` 永远排除在归档和仓库之外。
2. **环境变量引用**：`settings.yaml` 只保留环境变量名引用（如 `apiKeyEnv: BAI_API_KEY`），换机配置同名系统环境变量即可。
3. **设备路径模板化**：`{{DSH_HOME}}`、`{{DESKTOP}}`、`{{HOME}}` 必须在 export 时正确替换，apply 时自动渲染。
4. **不破坏目标端无关文件**：所有 apply 仅做增量覆盖或更新，不清理未登记的目标端文件。

---

## 输出契约

每次执行后报告：
- **操作模式**：Check / Update (Pull) / Upload (Push) / Install
- **体检前后差异对比摘要**
- **逐项执行命令与退出码**
- **四层状态矩阵汇总（Skills / Plugins / MCP / DSH Config）**
- **生效提示（是否需要重启 DSH）**
