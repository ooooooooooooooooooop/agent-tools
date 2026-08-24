# 示例：supervisor 悬挂优化的完整八步流程

来源：2026-08-24 真实实践——"严格好论文榜"长任务会话的优化全过程。展示八步怎么走。

## 第 0 步：量化基线

- 会话 JSONL 统计：9 个悬挂 supervisor（`attention_required`、daemon+claude 存活）；
- 44 分钟无看门狗段（10:08-10:52），WAIT→GET 轮询循环，8+ 次 interrupt 纠偏；
- 总输入 token 214 万 / 输出 10.2 万（21:1）；24 次上下文压缩；
- 12 次 supervisor 启动、25 次 codex 派工、40 次 wait、36 次 get。

## 第 1 步：全量列问题

- 9 个 supervisor 悬挂不回收；WAIT→GET 轮询变体；微观纠偏循环；打断方向丢失；无用户可见看门狗；token 浪费。

## 第 2 步：根因分类

| 问题 | 根因类型 |
|---|---|
| supervisor 悬挂 | 执行无法强制（"及时关闭"规则存在但无机制） |
| WAIT→GET 轮询 | 有规则没执行（铁律一存在但没拦截） |
| 微观纠偏循环 | 执行无法强制（interrupt 无预算） |

## 第 3 步：机制方案（拒绝临时）

- 临时方案（被否）：手动关闭 9 个 supervisor、下次注意；
- 机制方案：broker 层 zombie 自动回收 + interrupt 预算 + stall 参数必填。

## 第 4 步：业界调研

- Temporal Activity 超时/心跳/有限重试（docs.temporal.io）；
- Prefect 终态状态机 + TimedOut（docs.prefect.io）；
- OpenAI Agents SDK max_turns 预算（openai.github.io）；
- AWS Step Functions Timeout/Heartbeat/Retry（docs.aws.amazon.com）。

## 第 5 步：归纳取舍

- OpenAI max_turns → 移植为 `MAX_MANAGER_INTERRUPTS=2`；
- Temporal/Prefect 终态 → 移植为惰性 `zombie_reclaimed` 检测（状态读取时触发）；
- 取舍：只做这两条 + stall 必填，不做全局任务队列（成本过高）。

## 第 6 步：实施

- `managed_claude.py`：常量 + interrupt 预算检查 + `_maybe_reclaim_zombie`；
- `agent_broker_mcp.py`：stall_timeout 必填门禁；
- 测试：5 个新测试，MCP 回归 426 全过，四门禁全绿。

## 第 7 步：验证实装（三件套）

- hash：SKILL.md 与 ~/.dsh/skills 一致；
- 加载：broker 重启后 `list_managed_claude_supervisors` 返回 `zombie_reclaimed` 字段（新代码证据）；
- 行为：新 supervisor 启动必须传 stall（门禁生效）。

## 第 8 步：度量闭环

- 会话侧 token 统计（214 万输入/10.2 万输出）与调用次数已记录为基线；
- 后续观察：interrupt 第 3 次被拒（预算生效）、闲置 supervisor 自动回收、上下文压缩次数下降。
