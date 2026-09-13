# seed/ — 可携带世界模型材料（SANITIZED，可公开发布）

采用者起点包。与 PRIVATE pilot canonical 的关系：每个文件都是经
declassification 链（proposal → sanitizer → leakage check → authority
review）产出的衍生物，原件保持原等级；或本来就是为公开编写的模板。

## 内容

- `canonical/` — schema 1.1 空骨架。新 Personal AI 初始化起点：
  `entity_id`/`lineage` 等 `REPLACE_ME` 字段填写后即是一个合法 canonical
  （W/V/U0/U1/L 分层齐全，`history/` 为 append-only lineage 目录）。
- `M-sandbox-admission-gate.yaml` — 首个真实闭环产出的 SANITIZED L2 模型
  （沙箱准入层先于进程创建拒绝命令；缺输出≠目标不可用）。
  `derived_from` + `redaction_manifest` 完整，可作采用者 L2 先验种子。

## 使用

1. 复制 `canonical/` 为私有 canonicalDir，填写 `REPLACE_ME`。
2. （可选）把 `*.yaml` 模型种子并入 `world_model.models`——私有派生
   继承 SANITIZED 等级，可按本地策略再升严。
3. 跑 `canonical_compile.py --canonical <dir>` 生成首个 briefing。
4. 私有 L0/L1 记忆由采用者本地积累，永不进入本发布目录。
