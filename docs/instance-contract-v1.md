# Personal AI Instance Contract v1

状态：FROZEN 2026-09-14。本文件是产品层（personal-ai）对实例层（Personal-AI-Private）的唯一契约。
所有取值已定死，不接受等价替代写法。

## 1. 概念

```
instance = 一个 Personal AI 部署实例的私有资产全集
device   = 承载 instance 的某台机器（可换、可多机）
产品层   = personal-ai 公共仓（身体 + 公共灵魂契约），对 instance 只有可选挂载关系
```

## 2. 实例根 PERSONAL_AI_HOME

解析顺序（确定，无分支歧义）：

1. 调用方显式传入的 config `instance.root`
2. 环境变量 `PERSONAL_AI_HOME`
3. 标准默认目录 `~/.personal-ai`

三者为同一条解析链的先后级，不是可选项。产品代码禁止硬编码 `personal-ai-state`、
`~/personal-ai-state`、`~/.dsh/world-model` 任何具体实例路径；只允许
`实例根 + logical 相对路径`。

## 3. 身份

```
instance_id = UUID v4，bootstrap 时生成一次，写入 <root>/instance.yaml，永不变更
             属于实例，不属于设备；跨设备同步携带同一 instance_id
device_id   = 每设备独立，写入 <root>/devices/<device_id>.yaml
             禁止用 device_id 充当 instance_id
instance_schema_version = 1
```

`instance.yaml` 字段（schema 1）：

```yaml
instance_schema_version: 1
instance_id: <uuid4>
created_at: <iso8601>
ledger_kind: git        # 实例账本传输方式固定为 git
snapshot_transport: gdrive
```

## 4. 实例根布局

```
<root>/
  instance.yaml
  state/          identity / preferences / goals（这个人当前状态的声明式账本）
  memory/         Dynamic Memory records（可跨设备 merge）
  projects/       项目 state/overlay + index
  checkpoints/    长任务 durable 断点
  registry/       私有网关/实例绑定映射
  sync/           同步元数据、拓扑声明、device registry
  snapshots-index/ 指向 GDrive 快照的 receipt/index（不存快照本体）
  devices/        <device_id>.yaml（每机一条）
```

world-model canonical（W/V/U/L）**禁止**出现在实例根内——它的唯一 owner 是
world-model 私有研究仓。

## 5. Legacy 发现与一次性迁移

legacy roots 只用于 discovery，不作为永久 fallback：

```
legacy_roots = [ ~/personal-ai-state , ~/.dsh/world-model ]
```

判定规则（fail-closed）：

- 新根不存在 + 恰好一个 legacy 有数据 → 原子迁移 + hash/数量验收 + migration receipt
- 新根与 legacy 都有数据且内容一致 → 使用新根，legacy 写 retired 标记
- 新根与 legacy 都有数据且不一致 → fail closed，报 SPLIT_STATE，绝不自动 merge
- legacy 兼容最多保留一个迁移周期，之后 discovery 仍做、自动读禁止

## 6. 快照契约（GDrive 侧）

GDrive `Personal-AI-Private/snapshots/<ts>/` 的 manifest.json 字段固定：

```json
{
  "snapshot_schema_version": 1,
  "snapshot_id": "<ts>-<shortuuid>",
  "instance_id": "<uuid4>",
  "created_at": "<iso8601>",
  "state_revision": "<实例 git commit SHA>",
  "model_schema_version": "<L0-L3 schema 版本>",
  "source_versions": {"personal_ai": "<tag/commit>", "world_model": "<tag/commit>"},
  "layers": {
    "l0":         {"relpath": "l0/",         "sha256": "..."},
    "l1":         {"relpath": "l1/",         "sha256": "..."},
    "l2_private": {"relpath": "l2-private/", "sha256": "..."},
    "l3_private": {"relpath": "l3-private/", "sha256": "..."}
  }
}
```

规则：

- 快照 immutable：写完只读，永不就地修改
- manifest 只写 logical 相对路径，禁止设备绝对路径
- git 账本侧只在 `snapshots-index/` 存 receipt（snapshot_id + hash），
  不反向依赖 GDrive URL
- git 管活动状态，快照管恢复/模型负载；禁止把快照当第二套 live state

## 7. 一致性判定

```
同一 instance_id + 同一 state_revision + manifest hash 匹配 = 一致
任何字段对不上 = 不一致 → fail closed
instance_id 不同 = 不同实例 → 禁止 merge，人工裁定
```
