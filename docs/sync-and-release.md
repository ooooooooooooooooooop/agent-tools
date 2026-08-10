# 同步与发布

仓库是源端。同步是从已登记包到明确目的地的显式复制操作，例如 `C:\Users\<user>\.codex\skills`。

## 只读审计

```bash
python3 scripts/validate_repo.py --strict
python3 skill-quality-gate/scripts/quality_report.py --root . --strict
python3 scripts/sync_skills.py --destination "<destination>" --profile core --check
```

`--profile core` 只检查核心包；`--profile full` 检查完整集合；也可以使用一个或多个 `--skill` 做窄范围检查。脚本按文件 SHA-256 比较 missing、different、same 和 destination-only。

## 应用并复核

```bash
python3 scripts/sync_skills.py --destination "<destination>" --profile full --apply
python3 scripts/sync_skills.py --destination "<destination>" --profile full --check
```

脚本会原子复制缺失或不同的源文件，但永远不删除目的地额外文件。已从仓库移除的旧 Skill 如果仍在目的地，只能作为 destination-only 单独审查；删除它是另一个明确的高风险动作。

## 回滚

需要回滚时，从已知稳定的仓库提交或备份重新复制，再运行 `--check`。不要使用递归删除或整目录替换作为回滚手段。

## 发布门禁

1. 严格仓库校验通过。
2. Skill 质量门禁通过。
3. 回归测试和相关包 smoke test 通过。
4. Python 语法与 Markdown 检查通过。
5. 目标设备的只读同步检查通过。
6. `git diff --check` 和最终差异不包含私有/运行时文件。

仓库维护不会自动提交、推送、创建 PR、安装插件或修改全局配置。
