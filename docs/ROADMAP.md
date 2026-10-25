# SuperApp 规划

**文档日期：** 2026-07-31
**文档状态：** 当前规划（权威入口）
**上次变更：** LLM 底层由自研改为复用 NexusPilot 平台

## 1. 决策背景

原设计（package.json 描述"kanban board + LLM chat with function calling"）隐含自研 LLM 底层：本地 Prisma schema 已预留 `Conversation`/`Message` 模型（含 `toolCalls`、`affectedCardIds`、token 计数字段）。

**决策**：不再自研 LLM 基础设施，改为复用 NexusPilot 平台（`llm_fusion_platform`）。

理由：

- 平台已具备 superapp 所需的核心能力：统一 `POST /api/v1/responses`（普通 + SSE）、多供应商 Model Gateway、会话/消息存储、Run/Task 审计、费用与调用证据（详见平台 roadmap：Model Gateway 已完成，Phase 1 数据底座部分完成）
- 平台明确规划"为后续 Web 或其他业务应用提供可查询、可恢复、可审计的 API"（其 Phase 9：应用入口），superapp 是预期消费场景
- 自研将重复实现模型调用、会话存储、审计等平台已完成的工作，且平台明确不提供的部分（工具执行循环、Agent 工作流）正是 superapp 自身差异化的位置

**平台当前不可用能力**（规划中不得假设存在）：

- 工具执行循环（平台明确不在范围——由 superapp 侧执行）
- Context / Memory / Knowledge / Prompt / Evaluation（Phase 2 未实现）
- RabbitMQ Worker、Agent 工作流、MCP（暂停或未开始）

## 2. 架构边界

| 归属 | 能力 | 实现位置 |
|---|---|---|
| superapp | 看板领域数据（Board/Column/Card） | Prisma + SQLite（Electron）/ IndexedDB（Web，Phase 2 规划） |
| superapp | 看板 UI、聊天 UI | Next.js 组件 |
| superapp | 工具执行（function calling 中作用于看板的工具） | Next.js API Route 或服务端模块 |
| NexusPilot | 模型调用（普通/SSE）、会话与消息存储 | 平台 HTTP API |
| NexusPilot | Run/Task 审计、费用、调用证据 | 平台 HTTP API |

**调用路径**（假设，见 §5 待确认）：

```
聊天 UI → Next.js API Route 代理 → NexusPilot API（X-API-Key）
                                    │
              tool_call 事件 ────────┤（superapp 侧执行工具，结果回传为 tool 消息）
```

- API Key 只存在于 Next.js 服务端环境变量，浏览器与 Electron renderer 均不可见
- Electron 端与 Web 端共用同一代理路径，不单独直连平台

## 3. 与平台集成接口

依据平台 README（2026-07-31）与 roadmap 的现状：

| 用途 | 接口 | 状态 |
|---|---|---|
| 会话管理 | Session 新增/详情/筛选/更新 | 平台已实现 |
| 消息管理 | Message 不可变追加、详情、顺序分页 | 平台已实现 |
| 运行管理 | Run 创建、分页、取消 | 平台已实现 |
| 任务管理 | Task 创建、重试、状态过滤 | 平台已实现 |
| 模型生成 | `POST /api/v1/responses`（`stream: true` 即 SSE） | 平台已实现 |
| Provider 列表 | `GET /api/v1/providers` | 平台已实现 |

SSE 事件协议（固定）：`response.started` / `response.text.delta` / `response.tool_call.delta` / `response.usage` / `response.completed` / `response.failed`。

**需验证项**（写代码前确认，勿默认）：

- responses 请求需先创建 run（`run_id` 前置），superapp 的完整调用序列：创建 session → 追加用户消息 → 创建 run → responses 生成 → 追加助手消息（含 tool_call）→ 执行工具 → 追加 tool 角色消息 → 再次生成或结束。具体参数与顺序以平台代码/`/docs` 为准
- 工具调用结果回传的确切机制（平台 Message 支持 role 字符串与 `toolCalls` 字段，追加 tool 角色消息预计可行，需验证）

## 4. 阶段规划

### Phase 1（已完成）

看板骨架、Electron IPC 数据层、类型安全链。见 `CR-2026-06-16.md`。

### Phase 2：看板真实数据

- [ ] `getDb()` 数据流接入 Board/Column 渲染，移除硬编码三列
- [ ] 卡片 CRUD、列间移动、排序（IPC 补 update/delete/reorder 通道；Web stub 补 IndexedDB 实现）
- [ ] 子任务（Card 自引用）的读取与创建
- [ ] 卡片详情/编辑器（content 为 TipTap JSON 字符串，schema 已预留）
- [ ] 看板创建/切换（多 Board 支持）

### Phase 3：LLM 聊天接入

- [ ] NexusPilot API 客户端（Node 侧，types 与平台 contract 对应）
- [ ] Next.js API Route 代理（SSE 透传，API Key 仅存服务端）
- [ ] 聊天面板 UI（SSE 流式渲染，含 `response.text.delta` 增量）
- [ ] 会话管理（创建、历史列表、按平台 session/message 分页读取）
- [ ] 删除本地 `Conversation`/`Message` 模型（若 §5 确认；会话事实源在平台）
- [ ] 会话中看板上下文注入（当前看板/选中卡片作为 system 上下文）

### Phase 4：function calling

- [ ] 看板工具契约（最小集建议：`create_card`、`move_card`、`update_card`、`list_boards`/`list_cards`，参数 JSON Schema 与平台工具校验一致）
- [ ] 工具执行循环（superapp 侧）：接收 `response.tool_call.delta` → 校验参数 → 执行看板工具 → 结果作为 tool 消息回传
- [ ] 工具执行安全边界：只读工具与写工具分离；写操作需用户确认（UI 侧审批）或显式限制
- [ ] 执行结果标注 `affectedCardIds`，看板 UI 高亮被 LLM 改动的卡片

### Phase 5+（可选，依赖平台侧能力）

- [ ] 平台 Context/Memory/Knowledge 就绪后接入长期记忆与知识引用
- [ ] 多模型选择器（平台多供应商已就绪，superapp 仅需 UI）
- [ ] 平台 Agent 工作流（Phase 5）就绪后，将看板工具注册进平台工具层

## 5. 待确认问题

以下为影响文档与实现的决策点，默认值已按当前文档采用，用户确认后更新本文：

1. **本地 `Conversation`/`Message` 模型去留**（默认：删除，会话事实源在平台）
2. **代理路径**（默认：Next.js API Route 统一代理；备选：Electron 主进程直连平台 + Web 走代理）
3. **Phase 3 首版范围**（默认：仅聊天；备选：聊天 + 只读看板工具）
4. **平台部署为运行前置条件**（默认：是，README 已注明引用平台启动方式）
