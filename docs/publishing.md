# 发布到业界市场：可发布性矩阵与检查清单

> 结论先行：本仓库**不是全部可对外发布**。Skill 包经脱敏后可进社区/自建注册表；`mcp/agent-switchboard` 因 **PolyForm Noncommercial** 许可证 + windows-only + 本地 SQLite/CLI 依赖，**不能进商用市场**（Smithery/Glama 等）；DSH 插件无注册表，走 git/文档分发。发布前必跑 `python scripts/publish_check.py`。

## 1. 可发布性矩阵

| 包 | 类型 | 目标市场/渠道 | 当前阻碍 | 前置动作 |
|---|---|---|---|---|
| `skills/*`（13 个） | Agent Skill | Anthropic Agent Skills 社区、自建注册表、Git 仓库 | 中文为主；`weekly-work-summary` **裁定不发布**（个人办公、上海日历/固定路径/固定三人映射，设备绑定） | ① 去设备路径 ② 补英文文档 ③ 版本/元数据齐全；`weekly-work-summary` 已在 `publish_check.py` 的 `PUBLISH_EXCLUDED` 中豁免 |
| `mcp/agent-switchboard` | MCP server | Smithery / Glama / npm | **PolyForm Noncommercial**（商用市场禁止）、windows-only、依赖本地 SQLite + 本机 CLI（codex/claude） | 商用发布需上游换许可证；否则仅自托管非商用分发 |
| `dsh/*`（3 插件） | DSH 插件 | 无注册表，git/文档分发 | `cordis.patch.yml` 片段含设备路径 | 用 `sync_dsh_config.py --template --with-optional` 生成可移植片段 |

## 2. 为什么 agent-switchboard 不能直接上市场

1. **许可证**：`mcp.json` 声明 `PolyForm-Noncommercial-1.0.0`（修改版，非 MIT），商用市场（Smithery/Glama 免费层之外）直接违反。
2. **平台**：`platforms: ["windows"]`，市场通常要求跨平台。
3. **本地依赖**：入口是 Python 脚本 + PowerShell 安装器，依赖本机 `codex`/`claude` CLI 与本地 SQLite，不是无状态远程可托管的 MCP server。
4. **隐私**：会话快照/上下文读取属本地私有能力，外发需额外授权设计。

## 3. Skill 发布前的脱敏动作（对应改善 1 的模板渲染）

1. 扫描设备路径：`python scripts/publish_check.py`（失败项逐一清除）。
2. 硬编码路径模板化：参照 `skills/dsh-config-sync` 的 `{{DSH_HOME}}`/`{{DESKTOP}}`/`{{HOME}}` 机制。
3. 检查许可证：根 `LICENSE` 是否覆盖该 skill（不自持许可证的包默认随仓库许可证）。
4. 检查 README/示例：`SKILL.md` + `examples/*.md` 齐全（`run_skill_evals.py` 已兜底结构性）。
5. 版本与元数据：`skills.json` 中 `version`/`description`/`category`/`tier` 准确。

## 4. 发布检查清单（每次发布前逐项过）

```text
[ ] git status 干净，仅含待发布变更
[ ] python scripts/validate_repo.py --strict            # 结构 + 注册表一致
[ ] python skills/skill-quality-gate/scripts/quality_report.py --root . --strict   # 行为质量
[ ] python scripts/publish_check.py                      # 市场面：路径/许可证/文件齐全
[ ] python scripts/run_skill_evals.py                    # 结构性 evals
[ ] python -m unittest discover -s tests -v              # 仓库回归
[ ] python -m unittest discover -s mcp/agent-switchboard/tests -v   # MCP 回归
[ ] git diff --check                                     # 空白错误
```

## 5. 分渠道发布路径（当前建议）

| 渠道 | 状态 | 动作 |
|---|---|---|
| **自建注册表/私有 git** | ✅ 现成 | 仓库本身就是 SSOT，跨设备走 `sync_skills.py` / `environment-bootstrap` |
| **GitHub 公开仓库** | ⚠️ 可做 | 需先跑发布清单；建议把 `dsh-token-result.json`、根目录 `dsh-*.js` 审计脚本从发布集排除（`AGENTS.md` 已要求） |
| **Anthropic Skills 社区** | 🟡 待评估 | 补英文文档后挑选通用型 skill（task-mode-router、minimal-implementation、clarify-before-change） |
| **Smithery / Glama** | ⛔ 暂缓 | 等 agent-switchboard 上游换许可证或另写无状态版 |
| **npm/pip 分发** | 🟡 可选 | 把 skill 打包成 pip/npm 包需额外脚手架，收益待评估 |

## 6. 维护闭环

- 每次规则/插件/技能变更后跑发布清单（CI 已固化前三项 + evals）。
- 外部反馈（市场 Issue、用户使用）回流到 `docs/sync-and-release.md` 的变更记录。
- 定期用 `publish_check.py` 扫描新增包，防止设备路径悄悄混入。
