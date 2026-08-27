# Cordis 插件开发与固化指南

本指南规范在 DeepSeek Harness (DSH) 环境下动态 Cordis 插件的编写范式、Slot UI 模板与固化（Promote to Preset）全流程。

---

## 一、动态插件与持久化插件的分工

| 维度 | 动态插件（Dynamic Plugin） | 永久插件包（DSH Preset Plugin） |
|---|---|---|
| **存放位置** | 内存进程中（`cordis_define` / `cordis_run`） | 源码在 `dsh/<name>/`，挂载在 `~/.dsh/.agent-presets/<id>/` |
| **生命周期** | 单次进程生命周期，重启即释放 | 随 DSH 启动加载，永久生效 |
| **适用场景** | 临时可视化卡片、特定任务临时工具、会话级状态看板 | 平台级功能、常驻模型分类器、持久拦截器与重试器 |
| **编译特征** | 纯 ES6 JavaScript（无 TS / 无 JSX） | 纯 ES6 JavaScript（无本机绝对路径） |

---

## 二、标准 Slot UI 开发范式（纯 JS + React.createElement）

由于动态插件运行在浏览器 Client 容器内，不经过 Babel / JSX 编译，所有 React 组件必须使用 `React.createElement` 严格编写，并遵循 Fiber 资源释放纪律。

### 1. 通用状态看板 Slot 范例

```javascript
// Client-side Plugin
return {
  inject: ['slots'],
  apply(ctx) {
    const React = window.React
    if (!React) return

    function StatusCard(props) {
      return React.createElement('div', {
        style: {
          padding: '12px 16px',
          margin: '8px 0',
          borderRadius: '6px',
          border: '1px solid var(--border-subtle, #e5e7eb)',
          background: 'var(--bg-elevated, #f9fafb)',
          fontSize: '13px',
          lineHeight: '1.5'
        }
      }, [
        React.createElement('div', {
          key: 'title',
          style: { fontWeight: '600', marginBottom: '4px', color: 'var(--text-primary, #111827)' }
        }, props.title || '状态监控'),
        React.createElement('div', {
          key: 'desc',
          style: { color: 'var(--text-secondary, #4b5563)' }
        }, props.statusText || '运行正常')
      ])
    }

    // 注册至目标 Slot 并绑定生命周期 Dispose
    ctx.effect(() => {
      const unmount = ctx.slots.register('session-header-extra', (slotProps) => {
        return React.createElement(StatusCard, slotProps)
      })
      return () => unmount()
    })
  }
}
```

### 2. 生命周期与资源释放三红线

1. **不可串扰**：所有定时器（`setInterval` / `setTimeout`）、事件监听（`ctx.on`）必须包裹在 `ctx.effect` 中并返回清理函数；
2. **禁止序列化活对象**：严禁对 `ctx`、`session`、`service` 等 live data 直接使用 `JSON.stringify`，只提取需要的标量字段；
3. **安全服务调用**：使用 `ctx.get('serviceName')` 判空调用，或在插件顶层声明 `inject: ['requiredService']`。

---

## 三、动态插件固化流程（Promote to Preset）

当一个动态插件在当前会话验证成熟并需要长期沉淀时，按以下 3 步固化为仓库插件包并安装：

### 步骤 1：在仓库创建 `dsh/<plugin-name>/` 标准结构

```text
dsh/<plugin-name>/
  README.md            # 插件用途、依赖 Service、配置说明
  <plugin-name>.js     # 纯 JS 插件源码（禁止含本机绝对路径）
  cordis.patch.yml     # 可移植的 Cordis composition 挂载片段
```

### 步骤 2：编写可移植 `cordis.patch.yml`

```yaml
# cordis.patch.yml
plugins:
  - name: <plugin-name>
    path: ./<plugin-name>.js
    disabled: false
    config:
      autoStart: true
```

### 步骤 3：部署与安装至设备 Preset

1. 将 `dsh/<plugin-name>/` 复制到用户的 Agent Preset 目录：
   `${DSH_HOME:-$HOME/.dsh}/.agent-presets/<target-preset>/plugins/<plugin-name>/`
2. 将 `cordis.patch.yml` 片段合并至该 Preset 的 `cordis.yml`；
3. 重启或重新挂载 DSH Preset 即可永久生效。

---

## 四、官方内置插件挂载红线（2026-08-28 踩坑固化）

**挂载任何官方 `@deepseek-ai/*` 插件前，必须先查它是否已随 bundle 自动加载**——重复挂载同一插件 ID 会导致 Loader 组合失败、DSH 无法启动。

### 已验证随 `dsh-base` / `dsh-web-app` 自动加载、禁止在 `cordis.patch.yml` 显式挂载的官方插件

| 插件 ID | 功能 | 配置方式 |
|---|---|---|
| `compaction-tool-result-pruner` | tool_result 超长时纯算法头/中/尾裁剪（无模型调用） | 如需调 `thresholdChars` 等，用 `- id: <插件ID>` + `config:` 的**覆盖条目**，不要 `insert` |
| `dsh-token-meter` | `ctx.tokenMeter` 实时 Token 计量 | 同上 |
| `dsh-session-log-export` | Web UI 会话日志导出 | 同上 |

### 挂载前自查顺序

1. 读 profile 的 `package.json` → `dsh.profile.bundles`，确认 bundle 栈（如 `dsh-base`、`dsh-web-app`）；
2. 在 bundle 的 `cordis.patch.yml`（如 `node_modules/@deepseek-ai/dsh-base/cordis.patch.yml`）里 grep 目标插件 ID/包名；
3. 已存在 → 只用 `- id:` 覆盖条目调配置，或直接不动；不存在 → 才用 `- insert:` 挂载。

### 插件市场"已安装"计数语义

dshmarket（设置 → 插件市场）的"已安装"页**只统计 profile `package.json` 的 `dependencies`**（市场 UI / `dsh plugin add` 通道装的社区包，过滤 3 个内置 bundle）。`cordis.patch.yml` 补丁层挂载的插件（含本地文件插件 `./plugins/*.js`）**不在其账本内**，计数不变属正常。要看全部已加载插件（含 patch 层），用 **设置 → 插件 → 插件列表**（只读 Loader 清单）。
