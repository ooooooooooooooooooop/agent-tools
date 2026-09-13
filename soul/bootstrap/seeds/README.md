# bootstrap/seeds/ — 仅用于初始化的脱敏种子

准入条件（与 `models/` 的正式通用模型区分）：

- 仅用于新实体 bootstrap 的先验材料，**不是**已被正式采用的通用 L2/L3 模型。
- 必须经 declassification 链（proposal → sanitizer → leakage check →
  authority review）产出，或为公开编写；私有 canonical/L0-L1/未脱敏模型禁入。
- 内容示例：采用者首次建仓时可直接并入 `world_model.models` 的入门模型、
  演示用 open-loops 样例。
