---
name: publish-and-reuse
description: 完整发布与跨设备复现的一体化流程：一键发布门禁、三平面同步、新设备安装/更新 SOP。用于发布 Skill/脚本/插件/整个仓库、在新设备复现环境、跨设备增量更新，agent 自动加载即可执行，无需用户逐步教学。
version: 1.0.0
triggers:
  - "发布 Skill / 脚本 / 插件 / 整个仓库"
  - "跑发布门禁 / 检查可发布性"
  - "在新设备复现 / 安装我的环境"
  - "跨设备更新已安装的技能与配置"
not_for:
  - "同步 DSH 配置本体（~/.dsh 打包与恢复交给 dsh-config-sync）"
  - "把技能栈同步到 ~/.dsh/skills（交给 environment-bootstrap / sync_skills.py）"
  - "修改仓库内的具体包内容（本 skill 只做流程与门禁编排）"
depends_on:
  - skill-repository-maintainer
  - dsh-config-sync
  - environment-bootstrap
---

# 发布与跨设备复现（publish-and-reuse）

## 定位

本 skill 把"发布 + 跨设备复现"固化为 agent 可自动加载的流程：**仓库是唯一事实源（SSOT）**，所有发布与同步都以仓库为源。用户只需要说"发布"或"在新设备装一下"，agent 按本 skill 直接执行，不需要逐步教学。权威细节见 `docs/publishing.md`、`docs/sync-ongoing.md`、`docs/local-experience-and-cross-device-reuse.md`。

## 何时触发

- 用户要求"发布"仓库内容（Skill / 脚本 / DSH 插件 / 整个仓库）或"跑发布门禁"。
- 用户要求"在新设备安装 / 复现环境"、"跨设备更新"。
- 用户要求检查"什么能对外发布 / 发布到哪"。

## 一、完整发布（含门禁）

**一键执行（推荐）**：

```bash
python scripts/publish_all.py
```

它会按顺序聚合执行仓库发布门禁并汇总 PASS/FAIL，全部通过退出码为 0：

| 顺序 | 门禁 | 命令 |
|---|---|---|
| 1 | 结构 + 注册表一致 | `python scripts/validate_repo.py --strict` |
| 2 | Skill 行为质量 | `python skills/skill-quality-gate/scripts/quality_report.py --root . --strict` |
| 3 | 市场面（设备路径/许可证/文件齐全） | `python scripts/publish_check.py` |
| 4 | 结构性 evals（SKILL.md/openai.yaml/examples） | `python scripts/run_skill_evals.py` |
| 5 | 仓库回归 | `python -m unittest discover -s tests -v` |
| 6 | MCP 回归 | `python -m unittest discover -s mcp/agent-switchboard/tests -v` |
| 7 | 空白错误 | `git diff --check` |

任何一项 FAIL 时 `publish_all.py` 以退出码 1 结束并打印失败项与尾部日志，agent 应修复根因后重跑，**不要**把门禁失败当作交付物抛回用户。发布前确认 `git status` 仅含待发布变更（脚本对此输出提示，不判 FAIL）。

### 可发布性速查（决定"发到哪"）

| 包 | 能否对外发布 | 渠道 | 前置动作 |
|---|---|---|---|
| `skills/*` | ✅ | Anthropic Skills 社区、自建注册表、GitHub | ① 去设备路径 ② 补英文文档 ③ skills.json 元数据齐全；`weekly-work-summary` 已裁定不发布（个人办公、设备绑定，`publish_check.py` 已豁免） |
| `mcp/agent-switchboard` | ⛔ 禁止商用市场 | 仅自托管/非商用分发 | PolyForm Noncommercial 许可证 + windows-only + 依赖本机 codex/claude CLI 与本地 SQLite |
| `dsh/*` | 🟡 无注册表 | git / 文档分发 | 用 `sync_dsh_config.py --template --with-optional` 生成可移植片段 |

## 二、新设备首次安装（7 步）

```powershell
# 1. 装 DSH，配置 provider 与同名环境变量（BAI_API_KEY / CPA_API_KEY 等，密钥永不进包）
# 2. 克隆仓库
git clone <私有远程> C:\Users\<bob>\Desktop\skills

# 3. 源端自检
python scripts\validate_repo.py --strict
python scripts\run_skill_evals.py

# 4. 技能栈同步（先只读差异，再显式应用）
python scripts\sync_skills.py --destination "$env:USERPROFILE\.dsh\skills" --profile core --check
python scripts\sync_skills.py --destination "$env:USERPROFILE\.dsh\skills" --profile core --apply

# 5. 配置骨架：A 机 export（含 SHA-256 manifest + 敏感扫描）→ 拷贝归档到 B 机 → check → apply
#    A 机：python skills\dsh-config-sync\scripts\sync_dsh_config.py export --source ~/.dsh --display dsh-archive --template --with-optional
#    B 机：python skills\dsh-config-sync\scripts\sync_dsh_config.py check --display dsh-archive --destination ~/.dsh
#         python skills\dsh-config-sync\scripts\sync_dsh_config.py apply --display dsh-archive --destination ~/.dsh

# 6. 修补绝对路径耦合（见"已知缺口"）
# 7. 冒烟验证：跑一个真实会话；python scripts\publish_check.py
```

> `sync_skills.py` 与 `sync_dsh_config.py` 都遵守 check → apply 两阶段：apply 会写目标端用户级目录，必须由用户显式授权后执行。

## 三、跨设备增量更新（日常优化后）

```powershell
# A 机：提交并推送
git add . && git commit -m "feat: ..."
git push

# B 机：拉取 + 同步
git pull
python scripts\sync_skills.py --destination "$env:USERPROFILE\.dsh\skills" --profile full --check
python scripts\sync_skills.py --destination "$env:USERPROFILE\.dsh\skills" --profile full --apply
# 配置变更：重新 export → copy → check → apply（sync_dsh_config.py）
```

## 四、安全纪律（每次发布/同步必查）

1. **凭据不出包**：`.credentials.yaml`、`sessions/`、`storages/` 强制排除，绝不进入发布集。
2. **环境变量引用**：`settings.yaml` 只带 `apiKeyEnv` 名（如 `BAI_API_KEY`），目标设备设同名 env。
3. **设备路径模板化**：`sync_dsh_config.py --template` 把设备路径渲染为 `{{DSH_HOME}}`/`{{DESKTOP}}`/`{{HOME}}` 占位符，恢复时渲染回目标设备值。
4. **不删目标额外文件**：`sync_skills.py` 与 `sync_dsh_config.py` 只做增量，不删除目标端未登记文件。
5. **SHA-256 校验**：export 记录 manifest，apply 后复核（模板化文件只验存在性）。

## 五、已知缺口（复现前需修补）

| 缺口 | 位置 | 修法 |
|---|---|---|
| 绝对路径耦合 | `profiles/web/cordis.patch.yml`、`weekly-work-summary`（`C:\Desktop\日报`/`共享`）、`agent.cordis.yml` | 参数化为 `$DSH_HOME`/`$HOME` 模板（参照 chezmoi 渲染） |
| 文档数字滞后 | 仓库 AGENTS.md 写"9 个"实为 13 个；mcp.json 与 MCP README 版本号不一致 | 顺手修正，provenance 单独复核 |
| 运行态数据 | 会话审计基线（sessions/JSONL）各设备独立 | 不迁移；审计脚本设计为每设备重建基线 |

## 输出契约

每次执行后报告：模式（publish / install / update）、逐项执行命令与退出码、PASS/FAIL 汇总、失败项与修复指引、剩余风险（如绝对路径、未发布项）。只有门禁全部 PASS 才报"发布/同步完成"。

## 验证

发布流程以 `python scripts/publish_all.py` 退出码 0 + 全项 PASS 为完成标准；设备复现以目标端 `sync_skills.py --check` / `sync_dsh_config.py check` 差异归零 + 冒烟会话可用为完成标准。
