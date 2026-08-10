# 仓库架构

本仓库是 Codex Skill 的源仓库和发布控制面，不是运行时工作区，也不发布用户数据。

## 三层边界

```text
源仓库层
  skills.json + 根目录 Skill 包
        |
        v
质量与发布层
  scripts/ + tests/ + CI + install profiles
        |
        v
运行时层
  C:\Users\<user>\.codex\skills
```

- `skills.json` 是注册表，包含包路径、版本、层级、依赖和安装 profile。
- 根目录 Skill 包是发布源。保持根目录布局，以兼容 `npx skills add ... --skill <name>`。
- `scripts/validate_repo.py` 检查结构、manifest、元数据、示例和链接。
- `skill-quality-gate/scripts/quality_report.py` 检查触发边界、输出契约、验证信号和包内质量门槛。
- `scripts/sync_skills.py` 使用 SHA-256 比较或复制已登记包，永不删除目标端额外文件。
- `.taskflow/`、`.grepai/`、`.claude/`、`node_modules/`、私有记忆、临时文件和生成物属于本地运行状态。

## 安装 profile

- `core`：任务路由、修改边界、仓库维护、环境恢复和质量门禁。
- `full`：所有已登记包，包括可选的专家模拟、自然改写和条件启用的重型任务流。
- `conditional` Skill 不应因为普通请求自动启动；是否启用由触发门禁决定。

## Skill 包边界

每个注册包必须自包含：`SKILL.md` 是入口，`agents/openai.yaml` 是 UI 元数据，`examples/` 至少有一个非空示例。详细规则放在 `references/`，确定性重复逻辑放在 `scripts/`，输出模板放在 `assets/`。

不要把私有记忆、缓存、依赖树、机器路径、周报产物或临时检查文件放入包内。仓库外的运行产物应放在单独的工作目录；当前仓库中的 `.taskflow/` 仅因任务流脚本约定而保留为被忽略状态。

## 变更流程

1. 修改或新增包，并更新 `skills.json` 与对应 profile。
2. 运行严格仓库校验和 Skill 质量门禁。
3. 运行相关包的 smoke test、回归测试和 Python 语法检查。
4. 检查 Git 差异和运行时文件边界。
5. 对目标设备运行 `scripts/sync_skills.py --check`。
6. 只有用户明确要求时才运行 `--apply`，应用后立即再次 `--check`。

Git 提交、推送、PR 创建和用户级同步是独立动作，不由仓库校验自动执行。
