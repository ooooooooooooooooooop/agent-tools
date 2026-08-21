# DSH 配置同步 — 纳入/排除清单与规范

本文件是本 skill（`dsh-config-sync`）的操作参考。它定义 `$DSH_HOME`（默认 `~/.dsh`）下哪些应纳入同步、哪些必须排除，以及敏感扫描规则。

## 数据源

- `$env:DSH_HOME`（若设置，否则 `$HOME/.dsh`）。

## 纳入（配置类）

| 文件/目录 | 类型 | 说明 | 涵盖敏感？ |
|---|---|---|---|
| `AGENTS.md` | 文件 | 用户全局偏好（`dsh-agent-instructions` 注入每个对话） | 无（纯偏好文本；如需内部细节请自行确认） |
| `settings.yaml` | 文件 | 模型 provider/路由；只应含 `apiKeyEnv` 环境变量名与 `baseURL` | 不含明文密钥 |
| `profiles/` | 目录 | 可选，需显式指定 | 视内容 |
| `.agent-presets/` | 目录 | 可选 | 视内容 |
| `patches/` | 目录 | 可选 | 视内容 |

> 注：`AGENTS.md` / `settings.yaml` 作为**骨架**提交进公开仓库；若某个文件意外包含敏感内容，应先在源端脱敏，若无法脱敏则整体排除并对用户告警。

## 强制排除（无论是否显式指定）

| 路径 | 原因 |
|---|---|
| `.credentials.yaml` | 真实凭据（API key 明文），公开仓库泄露即危及账户 |
| `sessions/` | 运行时会话日志 |
| `storages/` | 运行时存储 |
| 任何 `*.jsonl` / `*.log` | 运行态 |
| `skills/` | 归 `environment-bootstrap` 管理 |

## 敏感扫描规则

导出复制后，对归档包内每个文件：

1. 拒绝路径包含 `.credentials.yaml`、`sessions`、`storages`、`.jsonl`、`.log`、`__pycache__`。
2. 对文本文件扫描明显密钥模式（如 `api[_-]?key`/`secret`/`token` 后直接跟非“环境变量名”值）。若值看起来像一个真实密钥（非 `xxx_API_KEY` 之类的名字），标 FAIL。
3. `settings.yaml` 里 `apiKeyEnv: <NAME>` 视为安全（是环境变量名引用）；若出现 `apiKey:`（明文密钥字段）则标 FAIL。

## 校验（post-apply / export）

- 每个纳入文件计算 SHA-256，写入 `manifest.json`。
- 恢复后对目标端逐文件重算 SHA-256，与 `manifest.json` 比对；`missing`/`different`/`extra` 逐项列出。
- 目标端额外文件（仅源无对应项的）绝不在 apply 中删除，仅报告。

## 结果分级

- `PASS`：敏感扫描为空、post-apply SHA-256 全部匹配。
- `PARTIAL`：有明确的非阻塞目标差异（如目标端额外配置文件被保留）。
- `FAIL`：敏感扫描命中、或校验不一致、或未显式确认目标路径。
