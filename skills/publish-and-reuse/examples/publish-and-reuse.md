# 示例：完整环境一键上传与一键更新

## 目标

通过 `publish-and-reuse` skill 完成两大多设备协同核心场景：
1. **一键上传环境**（导出配置 ➔ 四层体检对比 ➔ 门禁 ➔ 提交推送 GitHub）。
2. **一键更新环境**（更新前体检 ➔ 拉取 GitHub ➔ 应用到 DSH ➔ 更新后复核）。

---

## 场景一：一键上传环境（Upload — Skills + Plugins + MCP + DSH 配置）

1. **导出 DSH 全局配置骨架**（自动脱敏与模板化）：
```powershell
python skills\dsh-config-sync\scripts\sync_dsh_config.py export `
  --source "$env:USERPROFILE\.dsh" --display dsh-config --template --with-optional
```

2. **上传前四层体检与发布安全门禁**：
```powershell
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check

python scripts\publish_all.py
```
期望输出：四层体检 PASS，发布门禁 `ALL GATES PASSED`（7 项全绿）。

3. **提交并推送到 GitHub**：
```powershell
git add -A
git commit -m "chore: upload environment (skills, plugins, mcp, dsh-config)"
git push
```

---

## 场景二：一键更新环境（Update — 从 GitHub 同步到 DSH）

1. **更新前体检**（对比本地与已安装基线）：
```powershell
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check
```

2. **从远程拉取最新代码**：
```powershell
git pull
```

3. **一键应用到 DSH 运行时**：
```powershell
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --profile full --apply

python skills\dsh-config-sync\scripts\sync_dsh_config.py apply `
  --display dsh-config --destination "$env:USERPROFILE\.dsh"
```

4. **更新后复核**：
```powershell
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --dsh-config-dir dsh-config `
  --mcp-cordis "$env:USERPROFILE\.dsh\profiles\web\cordis.patch.yml" `
  --profile full --check
```

---

## 结果

- **上传**：四层资产（Skills、Plugins、MCP、DSH Config）完整脱敏打包，门禁全绿，成功推送 GitHub。
- **更新**：拉取最新变更并全量同步到 DSH 运行时，更新前后体检闭环，差异归零。
- **提醒**：若更新包含插件或 MCP 配置修改，提示重启 DSH 进程即可加载生效。
