> **历史文档，非当前执行依据。** 本文保留 2026-07-31 零 LLM 看板阶段的设计、批准记录与任务原貌；下文任务、代理指令及提交步骤不构成本阶段执行授权。当前以 [工作安排与计划接续规划](../../产品规划-工作安排与计划接续.md) 为准。

# SuperApp 零 LLM 功能规划与设计

**文档日期：** 2026-07-31
**状态：** 已批准（用户确认）
**范围：** 不依赖 LLM 底层（NexusPilot）即可交付的功能；含 LLM 前瞻骨架（无需平台运行）

## 1. 背景与范围

superapp 的 LLM 底层决策已改为复用 NexusPilot 平台（见 `docs/ROADMAP.md`）。本设计覆盖平台就绪前可全部实现的零 LLM 依赖功能：

- 看板真实数据闭环（多 Board、列、卡片、子任务）
- Web 端 IndexedDB 持久化
- Electron 生产构建链路
- 工程质量（ESLint、测试基线、zustand）
- LLM 前瞻骨架（API 契约、mock adapter、代理路由、聊天 UI 骨架）

**明确不做**（本阶段）：真实模型调用、SSE 对接平台、工具执行循环、TipTap 富文本、子任务完成状态（schema 变更）、用户鉴权、跨环境数据同步。

**数据拓扑**：本应用是单用户本地应用，数据全部由用户在本应用内创建，无外部来源、无导入导出。两套环境各自独立持久化，**不做同步**：

| 环境 | 存储 | 说明 |
|---|---|---|
| Desktop | SQLite（dev: `apps/web/prisma/dev.db`；packaged: `userData/superapp.db`） | 本机文件 |
| Web | IndexedDB | 浏览器本地 |

**鉴权边界**：本设计不含用户鉴权——单用户本地场景无鉴权对象（数据在本地存储，IPC 已有 contextIsolation + 输入校验基线）。两条边界声明：

1. **LLM 代理路由**（`app/api/llm/*`）是服务端端点：仅限本地开发（默认 `localhost`）；若部署到非 localhost，必须前置鉴权（如用户口令），否则任何访问者都能消费配置的 API 凭据与费用
2. **多用户/多设备**超出本设计范围：当前数据库结构（Board 无 owner 字段）不支持数据隔离，需要时另行设计

## 2. 决策记录

| 决策点 | 结论 | 理由 |
|---|---|---|
| 范围 | 包含 LLM 前瞻骨架 | 平台就绪后仅换 adapter 即可接通 |
| 卡片编辑器 | 标题 + Markdown 描述（纯文本存储） | Trello 式轻量体验；原 TipTap JSON 约束随自研 LLM 决策撤销；纯文本对 LLM 调用更友好 |
| 移动交互 | @dnd-kit 拖拽 | 看板标准交互；乐观更新 + 失败回滚 |
| 状态管理 | 单一 zustand store | 依赖已有；跨实体操作（移动）需原子性 |
| 测试框架 | vitest + testing-library | Next 14 + jest 集成繁琐；fake-indexeddb 可测 web 层 |
| 存储 | Web: idb (IndexedDB)；Desktop: 既有 Prisma/SQLite | 与现有数据层抽象一致，不引入 sql.js/wasm 替代路径 |

## 3. 架构

```
UI 组件（Board / Column / Card / 对话框 / ChatPanel 骨架）
        │
        ▼
  boardStore（zustand，单一 store）
        │  actions：加载 / 创建 / 更新 / 删除 / 移动
        ▼
  lib/db（统一数据层接口，对称 CRUD）
  ├── electron.ts（IPC → Prisma/SQLite）  desktop 环境
  └── web.ts（IndexedDB）                 浏览器环境

LLM 前瞻（与上分离，独立目录）：
  lib/llm/ → types / client / mock / config
  app/api/llm/ → 代理路由（未配置时 503）
```

`Board.tsx` 的 `DEFAULT_COLUMNS` 硬编码删除，改由 store 提供。

## 4. 数据层接口契约

统一对称 CRUD，`lib/db/index.ts` 的 `DbClient` 类型扩为：

```ts
interface DbClient {
  boards: {
    list(): Promise<BoardData[]>;
    create(name: string): Promise<BoardData>;
    update(id: string, data: { name: string }): Promise<BoardData>;
    delete(id: string): Promise<void>;
  };
  columns: {
    list(boardId: string): Promise<ColumnData[]>;
    create(boardId: string, name: string): Promise<ColumnData>;
    update(id: string, data: { name: string }): Promise<ColumnData>;
    delete(id: string): Promise<void>;
    // 拖拽重排后一次提交整列新顺序（含绝对位置计算）
    reorder(boardId: string, orderedIds: string[]): Promise<void>;
  };
  cards: {
    list(columnId: string): Promise<CardData[]>;
    listChildren(parentId: string): Promise<CardData[]>;
    create(columnId: string, data: { title: string }): Promise<CardData>;
    update(id: string, data: Partial<Pick<CardData, 'title' | 'content' | 'labels' | 'dueDate' | 'parentId'>>): Promise<CardData>;
    delete(id: string): Promise<void>;
    move(cardId: string, toColumnId: string, toIndex: number): Promise<void>;
  };
}
```

**Electron 侧**（`apps/desktop/src/ipc/db.ts`）：

- 新增 IPC 通道：`db:boards:update/delete`、`db:columns:list/create/update/delete/reorder`、`db:cards:listChildren/create/update/delete/move`
- 校验沿用 `assertNonEmptyString`/`assertId`，补充：`move` 的 `toIndex` 非负整数校验；`update` 按字段白名单校验
- `move` 在 Prisma 事务内完成：改 `columnId` + 重算源列与目标列全部卡片 `order`
- **删除级联**：Prisma 对必填关系默认 `Restrict`，`boards.delete`/`columns.delete` 会因子记录被外键拒绝。实施时在 schema 为 `Column.board`、`Card.column`、`Card.parent` 关系添加 `onDelete: Cascade` 并执行一次 `db:migrate`；IndexedDB 实现在同一事务内级联删除（cards → columns → board）
- 现有 `wrapHandler` 错误封装复用，无改动

**Web 侧**（`apps/web/lib/db/web.ts`）：

- 用 `idb`（依赖新增，^8）实现同接口，三个 object store：`boards`/`columns`/`cards`，字段与 Prisma 模型同构
- 应用层维护一致性（IndexedDB 无外键）；`move` 在同一事务中更新两列
- 首次访问无数据时种子：创建"待办/进行中/已完成"三列默认看板
- `boards.create` 由抛错改为真实实现

**Electron 首启种子策略**（已确认）：`boards:list` 返回空时，由 store 层引导创建默认三列看板（复用同一种子逻辑），与 Web 端一致，两环境首启体验一致。

## 5. 功能设计

### 5.1 看板

- 顶栏：看板名称 + 切换器（下拉）+ 新建看板入口
- 新建看板默认创建三列（待办/进行中/已完成）
- 删除看板需确认

### 5.2 列

- 新建、重命名（内联）、删除（确认；删除含卡片）
- 列排序：@dnd-kit 横向拖拽列头

### 5.3 卡片

- 新建：列底部"添加卡片"按钮 → 默认标题"新卡片"进入编辑态（沿用现有 `Column.tsx` 按钮位置）
- 标题内联编辑：点击进入编辑，Enter 保存，Esc 取消
- 删除：卡片菜单 + 确认
- 卡片显示：标题 + 标签 chip + 截止日期（红色高亮逾期）+ 子任务进度
- 跨列与列内排序：@dnd-kit（`DndContext` + `SortableContext`，外层列容器 `droppable`）
- 移动采用乐观更新：本地先行落位，`move` 失败回滚原位置并提示

### 5.4 卡片详情对话框

- 标题（输入框）+ 描述（textarea 编辑，Markdown 预览切换）+ 标签（逗号分隔输入）+ 截止日期（date input）+ 子任务列表
- Markdown 渲染：`react-markdown`（依赖新增）
- 保存：对话框内修改仅本地暂存，"保存"按钮统一提交 `cards.update`；未保存关闭需确认或直接丢弃（采用丢弃 + 提示）
- schema 注释更新："TipTap JSON as string" → "Markdown 文本"（字段类型不变，无需迁移）

### 5.5 子任务

- `cards:listChildren` 通道 + 详情对话框内子任务列表：新增子任务（新建卡片 `parentId` 指向当前卡片）
- 子任务勾选与排序：**本阶段不做**（schema 无 `isDone`/子任务序字段，属 schema 变更，推后）

### 5.6 Electron 生产构建

- `next.config.mjs` 启用 `output: 'export'`，`assetPrefix` 验证 `file://` 加载（静态导出资源路径已知坑，需实测确认）
- `electron-builder`：Prisma 原生引擎需 `asarUnpack`；packaged 模式在 `userData` 建新库（`dev.db` 不随包分发），首次启动引导或自动建库
- 验证 `build:desktop` 全链路（web 静态导出 → electron-builder 打包 → 产物可启动）

### 5.7 工程质量

- **ESLint**：root `eslint.config.*` + `eslint-config-next` + `typescript-eslint`，覆盖 web/desktop/shared
- **测试**（vitest）：
  - `lib/db` web 层：`fake-indexeddb` + `idb`（种子、CRUD、move 事务）
  - `ipc/db.ts` 校验函数与 `move` 排序逻辑：纯单测
  - `boardStore`：mock `lib/db`，测 actions 与错误回滚
  - 组件冒烟：Board/Column/Card 渲染与交互主路径
- 现有 `typecheck` 脚本保留；CI 命令（如有）由用户确认后补充

### 5.8 LLM 前瞻骨架

- `lib/llm/types.ts`：平台契约类型（`ResponseRequest`、SSE 事件 `response.started/text.delta/tool_call.delta/usage/completed/failed`、`ProviderInfo`），依据平台 README
- `lib/llm/client.ts`：HTTP + SSE 解析客户端，base URL 与 API Key 从环境变量读（`NEXUSPILOT_BASE_URL`/`NEXUSPILOT_API_KEY`，服务端私有）
- `lib/llm/mock.ts`：mock adapter，按事件协议输出固定序列，供 UI 开发
- `app/api/llm/responses/route.ts`：代理；未配置 `NEXUSPILOT_BASE_URL` 时返回 503 `{ error: "NEXUSPILOT_BASE_URL not configured" }`，不挂起
- **代理暴露边界**：仅限 localhost 本地开发；非 localhost 部署需前置鉴权（见"鉴权边界"）
- 聊天 UI 骨架：右侧 ChatPanel（消息列表 + 输入框 + 发送按钮），mock 数据驱动；真实请求的 SSE 透传验证留待平台就绪

## 6. 实施阶段

| 阶段 | 内容 | 依赖 |
|---|---|---|
| A | 工程基础：ESLint、vitest 骨架、zustand 引入 | 无 |
| B | 数据层扩展：IPC 通道 + IndexedDB + 接口契约 | A |
| C | 看板功能：store 接线、多 Board、列/卡片 CRUD、详情对话框、拖拽、子任务 | B |
| D | Electron 生产构建 | B（C 后验证） |
| E | LLM 前瞻：契约 + mock + 代理 + ChatPanel 骨架 | A（并行于 C/D） |

阶段间按顺序推进，每阶段结束运行 `typecheck` 与测试。

## 7. 风险与需验证项

- **Electron + Prisma 打包**（阶段 D）：原生引擎 asar 打包是已知复杂点；需实测，必要时查 electron-builder 对 Prisma 的官方配置示例
- **静态导出 + `file://`**：Next `output: 'export'` 的资源路径需实测确认
- **IndexedDB 一致性**：无外键约束，`move`/`delete`（删列含卡片）需在应用层保证事务性
- **react-markdown / idb / @dnd-kit 版本**：安装时确认与 React 18 兼容版本，具体版本号以 `pnpm add` 解析为准

## 8. 依赖变更汇总

新增：`zustand`（已有，待用）、`idb`、`@dnd-kit/core`、`@dnd-kit/sortable`、`react-markdown`；开发新增：`vitest`、`@testing-library/react`、`@testing-library/user-event`、`jsdom`（或 happy-dom）、`fake-indexeddb`、ESLint 相关包。

## 9. 文档联动

- `README.md`：阶段状态更新
- `docs/ROADMAP.md`：P2/P3 拆分与本设计的指向（本设计 = P2 + P3 中与平台解耦部分）
- 完成后更新 `.workbuddy/memory/`（项目记忆）
