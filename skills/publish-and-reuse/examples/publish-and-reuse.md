# 示例：完整发布一次 + 在新设备复现环境

## 目标

把本仓库（skills 仓库，SSOT）完整发布一次（跑全部门禁并确认可发布性），然后在另一台设备上完成首次环境复现。

## 前置约定

- 仓库根：`C:\Users\admin\Desktop\skills`（即仓库本身）。
- 目标设备：新机器，尚未安装本环境。
- 强制排除：`.credentials.yaml`、`sessions/`、`storages/`、设备运行层目录。

## 阶段一：完整发布（A 机）

1. 确认 `git status` 仅含待发布变更。
2. 一键跑发布门禁：

```text
python scripts\publish_all.py
```

3. 期望输出（节选）：

```text
[1/7] validate_repo .................... PASS
[2/7] quality_report ................... PASS
[3/7] publish_check .................... PASS
[4/7] run_skill_evals .................. PASS
[5/7] tests ............................ PASS
[6/7] mcp_tests ........................ PASS
[7/7] diff_check ....................... PASS
ALL GATES PASSED
```

4. 按可发布性矩阵确认去向：`skills/*` 可发社区/自建注册表；`mcp/agent-switchboard` 不进商用市场；`dsh/*` 走 git 分发。
5. `git push` 到私有远程。

## 阶段二：新设备首次安装（B 机）

1. 安装 DSH，配置 provider 与同名环境变量（`BAI_API_KEY`、`CPA_API_KEY` 等，密钥不落盘）。
2. 克隆仓库：`git clone <私有远程> C:\Users\<bob>\Desktop\skills`。
3. 源端自检：`python scripts\validate_repo.py --strict`。
4. 技能栈同步（先 check 后 apply）：

```text
python scripts\sync_skills.py --destination "%USERPROFILE%\.dsh\skills" --profile core --check
python scripts\sync_skills.py --destination "%USERPROFILE%\.dsh\skills" --profile core --apply
```

5. 配置骨架：A 机 `sync_dsh_config.py export --template --with-optional` 导出归档 → 拷贝到 B 机 → `check` → `apply` → 再 `check` 核对 SHA-256。
6. 修补绝对路径：确认 `cordis.patch.yml` / `agent.cordis.yml` 已模板化，否则按 `{{DSH_HOME}}`/`{{HOME}}` 处理。
7. 冒烟：跑一个真实会话，确认技能可被 agent 加载、模型路由生效。

## 结果

- 发布：`ALL GATES PASSED`，仅发布脱敏内容，无设备路径与凭据。
- 复现：B 机技能栈差异归零、配置 SHA-256 校验 PASS、冒烟会话可用。
- 剩余风险：`weekly-work-summary` 不发布（设备绑定）；MCP 仅自托管非商用。
