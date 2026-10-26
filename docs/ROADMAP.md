# SuperApp 规划（看板阶段，历史）

> **本文已不是当前产品规划。** 自 2026-10-26 起，当前唯一规划依据是 [`产品规划-工作安排与计划接续.md`](产品规划-工作安排与计划接续.md)。本文只保留 2026-07-31 看板阶段的决策记录与早期路线，其中 Phase 2-5 未完成的项目不构成当前开发安排，也不代表当前产品范围。

**文档日期：** 2026-07-31
**文档状态：** 历史规划（仅适用于 2026-07-31 看板阶段）
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

## 4. 阶段规划（历史）

### Phase 1（已完成，历史）

看板骨架、Electron IPC 数据层、类型安全链。见 `CR-2026-06-16.md`。

### Phase 2 及以后（已过时，任务清单已删除）

2026-10-26 清理：原 Phase 2「看板真实数据」、Phase 3「LLM 聊天接入」、Phase 4「function calling」、Phase 5+「可选能力」的待办清单属于看板阶段，已被当前产品规划取代，任务项已删除。本文只保留当时的阶段划分记录，不再作为开发安排。当前任务见 [`开发安排.md`](开发安排.md)。

## 5. 待确认问题

以下决策点属于 2026-07-31 看板阶段，已不再需要当前确认，仅保留作历史记录；现行规划与待确认问题见 [`产品规划-工作安排与计划接续.md`](产品规划-工作安排与计划接续.md) 附录 C。

1. **本地 `Conversation`/`Message` 模型去留**（默认：删除，会话事实源在平台）
2. **代理路径**（默认：Next.js API Route 统一代理；备选：Electron 主进程直连平台 + Web 走代理）
3. **Phase 3 首版范围**（默认：仅聊天；备选：聊天 + 只读看板工具）
4. **平台部署为运行前置条件**（默认：是，README 已注明引用平台启动方式）
