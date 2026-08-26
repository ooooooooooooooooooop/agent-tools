# 示例：完整环境上传与跨设备增量更新

## 目标

通过 `publish-and-reuse` skill 完成两类最常见场景：
1. **场景一（上传备份）**：将本机的最新配置、技能、插件与 MCP 状态脱敏打包并推送到 GitHub。
2. **场景二（增量更新）**：在另一台机器上从 GitHub 拉取更新，一键应用到运行时环境。

---

## 场景一：一键上传/备份环境到 GitHub

1. **导出 DSH 全局配置骨架**（自动脱敏与模板化）：
```powershell
python skills\dsh-config-sync\scripts\sync_dsh_config.py export `
  --source "$env:USERPROFILE\.dsh" --display dsh-config --template --with-optional
```

2. **跑一键发布与质量门禁**：
```powershell
python scripts\publish_all.py
```
期望输出：`ALL GATES PASSED`（7 项全绿）。

3. **提交并推送到 GitHub**：
```powershell
git add -A
git commit -m "chore: backup environment (skills, plugins, mcp, dsh-config)"
git push
```

---

## 场景二：一键从 GitHub 更新并同步到 DSH

1. **拉取远程最新代码**：
```powershell
git pull
```

2. **一键同步 Skills + DSH 插件**：
```powershell
python scripts\sync_skills.py `
  --destination "$env:USERPROFILE\.dsh\skills" `
  --plugins-destination "$env:USERPROFILE\.dsh\profiles\web\plugins" `
  --profile full --apply
```

3. **恢复 DSH 全局配置**（自动渲染路径）：
```powershell
python skills\dsh-config-sync\scripts\sync_dsh_config.py apply `
  --display dsh-config --destination "$env:USERPROFILE\.dsh"
```

4. **一键四层聚合体检复核**：
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

- 上传：配置完成路径模板化脱敏，敏感扫描 PASS，全量门禁全绿，成功推送到 GitHub。
- 更新：Skills 18/18 PASS、DSH 插件 5/5 PASS、DSH 配置一致、MCP 路径有效。
- 提醒：若涉及插件或 MCP 配置修改，重启 DSH 进程即可加载新环境。
