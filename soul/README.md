# soul/ — Personal AI 可携带认知状态（公开发布）

`soul/` 是 Personal AI 发布仓的认知模型部分：与机制层（`dsh/world-model/`
等插件/skills/MCP）配对——机制是身体适配器，soul 是可随身体迁移的
W/V/U/L 认知状态。**每次发布 = 一次认知模型更新**，由 `manifest.json`
锚定（soul_version / schema_version / 模型清单 / briefing 版本 /
release commit）。

与 PRIVATE pilot canonical 的关系：本目录每个文件都是经 declassification
链（proposal → sanitizer → leakage check → authority review）产出的
衍生物，原件保持原等级；或本来就是为公开编写的模板。私有 L0/L1 记忆
（ledger/runs/sessions）与私有 L2/L3 永远不进入本目录——它们属于
采用者本地积累 + gdrive 私有备份。

## 布局

```
soul/
├── manifest.json        # 发布锚：soul_version/schema_version/模型清单/briefing 版本
├── schema/              # 结构契约
│   └── canonical.schema.json   # current.yaml schema 1.1（对应 migrate_v031 强制的字段集）
├── briefing/            # briefing 编译输出契约
│   └── template.md      # 7 段固定结构 + 容量/裁剪规则
├── models/              # 通用可携带认知模型（脱敏衍生物）
│   ├── l2/              # L2 世界行为模型（如 M-sandbox-admission-gate）
│   └── l3/              # L3 元认知模型
└── bootstrap/           # 采用者起点包
    ├── canonical/       # schema 1.1 空骨架（REPLACE_ME 字段填写后即为合法 canonical）
    └── seeds/           # 仅用于 bootstrap 的脱敏种子（区别于 models/ 的正式模型）
```

## 采用者使用

1. 复制 `bootstrap/canonical/` 为私有 canonicalDir，填写 `REPLACE_ME`
   （`entity_id`/lineage 等；W/V/U0/U1/L 分层齐全，`history/` 为
   append-only lineage 目录）。
2. （可选）把 `models/` 下模型并入 `world_model.models`——私有派生
   继承 SANITIZED 等级，可按本地策略再升严。
3. 跑 `dsh/world-model/canonical_compile.py --canonical <dir>` 生成
   首个 briefing（契约见 `briefing/template.md`）。
4. 私有 L0/L1 记忆由采用者本地积累，永不回流到本目录。
