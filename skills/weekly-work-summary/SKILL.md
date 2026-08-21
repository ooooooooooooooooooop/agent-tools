---
name: weekly-work-summary
description: "按中国上海时间和中国实际工作日，从 C:\\Desktop\\日报 的三人日报生成固定四列表格，并覆盖保存到 C:\\Desktop\\共享；用于本周工作汇总、三人日报汇总和工作总结 Excel。"
---

# 固定周报汇总

用于用户要求“本周工作汇总”“从日报生成 Excel”“三人周报”或同义请求时。流程固定，不临场改写结构、不另找数据源、不生成备用文件名。

## 适用范围与不适用边界

- 适用：固定三人日报、上海时间周报和中文工作总结 Excel。
- 不适用：单人日报改写、任意项目周报、非中国工作日历、不同人员映射或不同输出布局；这些请求不得套用本 Skill 的固定脚本。

## 固定契约

- 时区：`Asia/Shanghai`。
- 日期：默认按当前上海日期所在周计算周一至周日，再按内置中国实际工作日历筛选；当前 Skill 内置 2026 年国务院调休口径，未内置年份直接失败，不猜测。
- 输入目录：`C:\Desktop\日报`，只读取目录顶层文件。
- 人员与文件映射（日期范围必须与本周实际工作日首尾日期一致）：
  - `陈鹏`：`日报-陈鹏M.D-M.D.xlsx`
  - `胥帅杰`：`胥M.D-M.D.xlsx`
  - `徐文彬`：`论文创新统计工作总结_YYYYMMDD-YYYYMMDD.xlsx`
- 输出：`C:\Desktop\共享\工作总结_YYYYMMDD-YYYYMMDD.xlsx`，日期使用本周实际工作日首尾日期。
- 工作表：`工作总结`；列固定为 `工作日期、陈鹏、胥帅杰、徐文彬`。
- 每个工作日每个人单元格必须是一句中文总结；源文本没有句末标点时只补一个 `。`，不重新编造或合并事实。
- 目标文件已存在时，固定覆盖精确目标路径；覆盖前先检查目录、路径类型和独占占用，不能写入时报告准确原因，不改名、不静默跳过。

## 固定执行

1. 调用 `codex_app__load_workspace_dependencies`，使用返回的 Node.js 和 `node_modules`，不得使用系统或项目外的表格库。
2. 在会话临时目录建立 `node_modules` Junction，运行本 Skill 的 `scripts/generate_weekly_work_summary.mjs`；正常运行不传参数。
3. 只有在验证或明确历史重跑时才传 `--week-start YYYY-MM-DD`，且必须是周一；该参数不改变生产输出契约。
4. 脚本会严格检查三份源文件、工作日行数、中文句子、目标目录、目标占用、导出后读回值、公式错误和渲染结果；任一检查失败即失败关闭。

运行脚本前，若当前表格工作流要求操作标记，先执行一次：

```powershell
node <spreadsheets-skill>\container_tools\mark_artifact_operation_started.mjs --operation-kind edit --expected-output-count 1 --output-format xlsx
```

## 输出与失败报告

成功时只交付目标 Excel 的可点击路径，并简要说明三人四列、工作日行数和验证结果。失败时报告：上海日期范围、缺失或异常的源文件/行数、目标路径、目录不可用或文件占用的原始原因；不得把旧文件、`latest` 文件或部分导出当作成功。

脚本是唯一生成入口：

- [generate_weekly_work_summary.mjs](scripts/generate_weekly_work_summary.mjs)
