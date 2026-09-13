# CORE 模式示例：一次真实 bug 修复

任务：修复 `tools.read()` 在带 BOM 的 UTF-8 文件上偶发解码失败。

```text
WM_ACTIVATE      模式=CORE；理由：跨会话可复现 bug，修复需可验证预测
STATE_RESTORE    读 ~/personal-ai-state/world-model/current.yaml → 空模型（首次）
CURRENT_MODEL    BOM 文件在部分 Windows 工具链上以 GBK 回退解码（推断，置信低）
PREDICTION_CREATED
  P1: errors='replace' 的 read 调用会在 BOM 文件上产出 mojibake → refutable
  P2: 显式 encoding="utf-8-sig" 后同一文件返回正确文本 → refutable
ACTION           复现脚本 → 真实输出
OBSERVATION_RECORDED  P1 refuted（replace 未产出 mojibake，是 reader 端 GBK 解码 UTF-8 字节才炸）
UPDATE_APPLIED   模型修订：问题在 consumer 解码侧而非 producer 缺省——结构级修订
STATE_PERSISTED  ledger append：P1 refuted 证据=session-x seq42；operators.yaml 候选：
                 「producer/consumer 双向显式编码契约」provisional
```

关键点：预测写在取证之前；refuted 预测直接修订模型而非圆场。
