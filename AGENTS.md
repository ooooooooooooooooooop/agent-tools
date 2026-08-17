# AGENTS.md — 本仓库浏览与改动导航

这份文件给**进入仓库的代理/协作者**一个最快的心智模型：这里有什么、哪些能碰、哪些绝不能碰、改动会被哪些检查拦截。详细契约见 [`docs/architecture.md`](./docs/architecture.md)。

## 一句话

这是一个 **Agent 工具的源码与发布控制面**（Skill 包 + MCP 发行包 + 质量脚手架），**不是运行时工作区**。根目录 top-level 目录属于四层之一。

## 四层目录（改动前先认层）

| 层 | 目录 | 可否改动 | 改动要点 |
|---|---|---|---|
| ① Skill 包 | `skills/<name>/`（9 个） | ✅ | 必须登记在 `skills.json`；布局统一 `SKILL.md + agents/openai.yaml + examples/*.md` |
| ② MCP 包 | `mcp/<name>/` | ✅ | 登记在 `mcp.json`；自持子目录许可证；`agent-switchboard` 是修改版，非 MIT |
| ③ 质量脚手架 | `scripts/` `tests/` `docs/` `_template/` `.github/workflows/`（`skill-quality-gate` 是 ① 中的 Skill 包，位于 `skills/skill-quality-gate/`） | ✅ | 改 `validate_repo.py` 会影响全部上层契约，谨慎 |
| ④ 设备运行层 | `.taskflow/` `.grepai/` `.claude/` `node_modules/` 等 | ❌ 禁改/禁提交 | 本地运行态，发布门禁拒绝 |

## 硬约束（改目录结构的红线）

1. **Skill 统一收在 `skills/<name>/` 下**。`scripts/validate_repo.py::discover_skill_dirs()` 识别 `skills/` 子目录里含 `SKILL.md` 的目录（为兼容也认可根目录平铺的旧包）。新增 Skill 放进 `skills/` 并登记到 `skills.json` 的 `path`（`./skills/<name>`）；两处不同步会让 `--strict` 判 FAIL。
2. **两个注册表必须与目录一致**：`skills.json` ↔ `skills/*`；`mcp.json` ↔ `mcp/*`。校验器逐一核对。
3. **发布门禁**：`git diff --check`、`python scripts/validate_repo.py --strict`、`python skills/skill-quality-gate/scripts/quality_report.py --root . --strict`、仓库回归、MCP 回归全绿才可合并。

## 改动后必须跑的检查

```bash
python scripts/validate_repo.py --strict          # 结构 + 注册表 + 许可证 + 链接 + 运行时文件边界
python skills/skill-quality-gate/scripts/quality_report.py --root . --strict   # Skill 门禁
python -m unittest discover -s tests -v           # 仓库回归
python -m unittest discover -s mcp/agent-switchboard/tests -v           # MCP 回归
git diff --check                                  # 空白错误
```

## 本地缓存与运行态（当前仓库的"大块头"）

- `mcp/agent-switchboard/_sdk_probe_deps/` 曾放 Claude Agent SDK 缓存（约 360MB，含打包的 claude.exe）。**已移到** `~/.cache/agent-switchboard-sdk/`，`claude_sdk_backend.py` 从那里探测（可用 `AGENT_BROKER_CLAUDE_SDK_DEPS` 覆盖）。仓库不应再出现这种大体积缓存目录。
- 任何 `state.sqlite`、`.jsonl`、`.log`、用户路径、会话/任务 ID、模型/认证信息都是运行时态，**禁止**由 `mcp.json` 或 CI 校验放行到发布集。
