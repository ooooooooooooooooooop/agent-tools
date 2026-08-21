# 示例：把 DSH 配置从本机同步到另一台设备

## 目标

把 `C:\Users\yexue\.dsh` 下的用户偏好与模型路由配置打包成脱敏骨架，提交到（公开）仓库，并在新设备上恢复。

## 前置约定

- 源 DSH 主页：`C:\Users\yexue\.dsh`（`$env:DSH_HOME` 确认）。
- 归档落点：仓库 `dsh-config/` 目录（配置骨架随仓库提交）。
- 强制排除：`.credentials.yaml`、`sessions/`、`storages/`、`skills/`。

## 打包（export）

1. 确认 `settings.yaml` 只含 `apiKeyEnv`（环境变量名）与 `baseURL`，无明文密钥。
2. 收集 `AGENTS.md`、`settings.yaml`；如需要再显式加 `profiles/`。
3. 复制到 `dsh-config/`，跑敏感扫描：确认没有 `.credentials.yaml`、`.jsonl`、`.log`、可选 `apiKey` 明文。
4. 写 `manifest.json`（路径 + SHA-256）。
5. 提交到仓库（骨架）。

```text
dsh-config/
  AGENTS.md
  settings.yaml
  manifest.json
```

## 恢复（restore）

1. clone 仓库到新设备，目标 `C:\Users\<新用户>\.dsh`。
2. `check`：只读对比 missing/different/extra。
3. `apply`：复制覆盖。
4. 在新设备设置环境变量（`BAI_API_KEY`、`KIMI_CODING_API_KEY`、`CPA_API_KEY`）——不放入配置包，凭据走环境变量。
5. post-apply 校验 SHA-256，报 `PASS` / `PARTIAL`。

## 结果

- `sensitive-data scan: PASS`
- `apply: completed`
- `post-apply SHA-256 check: PASS`
- 未携带任何密钥与运行态。
