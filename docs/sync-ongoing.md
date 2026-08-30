# 优化同步 SOP（2026-08-22）

> 本仓库作为唯一事实源（SSOT），所有技能/脚本/插件的优化先改仓库，再从仓库同步到本机安装目录和其他设备。

## 源头结构（谁是谁的 SSOT）

| 层 | 事实源 | 安装位置 | 同步工具 |
|---|---|---|---|
| Skill 包 | `<repo-root>\skills/*` | `~/.dsh/skills/*` | `sync_skills.py` / `environment-bootstrap` |
| 脚本/校验器 | `<repo-root>\scripts/*` | 仓库就地（git clone） | git |
| DSH runtime composition 源码 | `<repo-root>\dsh/*`、`registry/harnesses/dsh.yaml` | `~/.dsh/runtime`、`~/.dsh/profiles/web` | `aic apply dsh` |
| 配置骨架（AGENTS.md/settings/profiles/presets） | `~/.dsh/` | `~/.dsh/` | `sync_dsh_config.py`（export→check→apply） |
| **preset 随附技能**（cordis-plugin-development / editing-cordis-compositions） | `~/.dsh/.agent-presets/cc/skills/*`（随 cc 预设分发） | 随预设安装在 `~/.dsh/.agent-presets/<id>/skills/*` | `sync_dsh_config.py --with-optional`（随配置骨架） |
| MCP 包 | `<repo-root>\mcp/*` | 仓库就地（按需安装） | git |
| CI/工作流/文档 | `<repo-root>\.github/*` `docs/*` | 仓库就地 | git |

> **preset 随附技能说明**：这两个是**部署专用**技能，教 Agent 操作 DSH 自身的动态插件（cordis_define/run/stop）与静态组合（cordis.yml / preset 两平面）。它们**只对运行 DSH + cc 预设的设备有意义**，随 `.agent-presets/` 配置骨架同步；**不进 skills.json、不走 sync_skills.py**。非 DSH 设备不需要、也不应同步（内容依赖 DSH 专属运行时，同步过去是死代码）。

## 日常优化循环（每次优化后）

```
[1] 改仓库源码
    └─ scripts/ 或 skills/<name>/ 或 dsh/<name>/ 或 docs/
    └─ 如果改 skills.json/mcp.json，需同步更新注册表
    └─ 如果改 ~/.dsh 配置，需同步更新仓库归档或走 dsh-config-sync
    └─ DSH runtime/base/UI/overlay 改动统一走 `aic apply dsh`，不走手工复制

[2] 本地跑门禁
    └─ git add ...; .githooks/pre-commit
    └─ （或手动：validate_repo + quality_report + unittest + evals + diff-check）
    └─ 全部 PASS 才能提交

[3] git commit → git push（到私有远程）

[4] 本机安装目录刷新（如果改了 skill 包）
    └─ python scripts/sync_skills.py --destination "%USERPROFILE%\.dsh\skills" --profile core --check
    └─ python scripts/sync_skills.py --destination "%USERPROFILE%\.dsh\skills" --profile core --apply
```

## 跨设备同步（首次 + 增量）

### 首次拿到新设备

```powershell
# 1. 装 DSH + 配置 provider 环境变量
# 2. 克隆仓库
git clone <私有远程> C:\Users\<bob>\Desktop\skills

# 3. 源端自检
python scripts\validate_repo.py --strict
python scripts\run_skill_evals.py

# 4. 技能栈同步到安装目录（只读差异）
python scripts\sync_skills.py --destination "%USERPROFILE%\.dsh\skills" --profile core --check
# 确认 missing/different/extra，然后：
python scripts\sync_skills.py --destination "%USERPROFILE%\.dsh\skills" --profile core --apply

# 5. 配置骨架：从 A 机导出再包过来
#    A 机：python skills\dsh-config-sync\scripts\sync_dsh_config.py export --source ~/.dsh --display dsh-archive --template --with-optional
#    拷贝 dsh-archive/ 到 B 机
#    B 机：python skills\dsh-config-sync\scripts\sync_dsh_config.py check --display dsh-archive --destination ~/.dsh
#         python skills\dsh-config-sync\scripts\sync_dsh_config.py apply --display dsh-archive --destination ~/.dsh

# 6. 冒烟验证
python scripts\publish_check.py
```

### 增量同步（日常优化后）

```powershell
# A 机：提交并推送
git add . && git commit -m "feat: ..."
git push

# B 机：拉取 + 同步
git pull
python scripts\sync_skills.py --destination "%USERPROFILE%\.dsh\skills" --profile full --check
python scripts\sync_skills.py --destination "%USERPROFILE%\.dsh\skills" --profile full --apply
# 配置变更：重新 export → copy → check → apply
```

## ✅ 已解决：execution-discipline 已补登记（2026-08-22）

`execution-discipline` 原只在 `~/.dsh/skills` 安装、仓库没有。已按下列步骤补登记完成：

```text
1. 复制 ~/.dsh/skills/execution-discipline → skills/execution-discipline（SKILL.md + agents/openai.yaml）
2. 补 examples/discipline-in-action.md（满足 evals 结构性要求）
3. 登记 skills.json：core + full profile + skills 数组（tier=core, P0）
4. README.md 补 [execution-discipline] 公开介绍行（满足仓库回归测试）
5. 全部门禁通过：validate 14/14、quality 14/14、evals 14/14、publish PASS、unittest 10 OK
```

此后优化 `execution-discipline` 一律改仓库副本，再走下方日常循环同步。

## 一键同步脚本（可选）

如果想把本机安装刷新 + 配置导出合并成一条命令，可以创建一个 `scripts/sync_all.py`：

```python
# python scripts/sync_all.py
# 功能：从仓库同步 skill 到 ~/.dsh/skills + 导出配置骨架到 dsh-config/
# 接受 --target (本机|export-only) 等参数
# 需要时再写
```

## 安全纪律（每次同步必查）

- ✅ 凭据不出包：`.credentials.yaml` 永不在同步范围内
- ✅ 环境变量引用：`settings.yaml` 只带 `apiKeyEnv` 名，目标设备设同名 env
- ✅ 设备路径模板化：`sync_dsh_config.py --template` 处理
- ✅ 不删目标额外文件：`sync_skills.py` 和 `sync_dsh_config.py` 都遵守
- ✅ SHA-256 校验：export 记录，apply 后验，模板化文件只验存在性
