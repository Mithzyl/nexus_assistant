# 04 API 与 SSE 契约（以当前运行代码为准）

更新时间：2026-10-23。本文替换此前关于 `/principals/resolve`、`X-NexusPilot-*`、
`agent_profile` 的旧设计。当前 NexusPilot 代码位于
`/Users/mith/Desktop/project/llm_fusion_platform` 的
`features/identity/routers/application_runs.py` 和
`features/agent_runtime/routers/agent_workflows.py`。

## 1. 上行边界（assistant-api → NexusPilot）

```text
Base path        /api/v1/app
认证             Authorization: Bearer <application credential>
应用标识          X-Application-Id: personal_assistant
用户断言          X-External-User-Id: <PA user_id>
请求/响应         application/json; charset=utf-8；工作流启动为 text/event-stream
```

三条规则：

1. `X-External-User-Id` 不是客户端凭据，而是 assistant-api 在验证本地会话后写入的服务端断言。
   NexusPilot 在认证应用时自动解析或创建该应用用户；**没有** `/principals/resolve`。
2. 请求体没有 `user_id`、`principal_id`、平台用户 ID。客户端也不能提交这些字段。
3. 应用凭据、provider/model 只存在服务端配置；浏览器响应不返回它们。

## 2. 当前端点

| 能力 | 方法与路径 | 说明 |
|---|---|---|
| 创建 Run | `POST /runs` | body 仅 `{"user_request": "..."}`；201 返回 RunRead |
| 启动工作流 | `POST /runs/{run_id}/agent-workflows` | `stream=true` 返回 SSE；body 见下 |
| 查询 Run 工作流 | `GET /runs/{run_id}/agent-workflow` | 摘要 |
| 查询工作流 | `GET /agent-workflows/{id}` | 摘要 |
| 订阅事件 | `GET /agent-workflows/{id}/events` | 已持久化事件的有限回放，支持 `Last-Event-ID` |
| 查询结果 | `GET /agent-workflows/{id}/result` | 终态后返回完整结果 |
| 取消 | `POST /agent-workflows/{id}/cancel` | 返回摘要；不保证远端节点立即停止 |
| 分页节点 | `GET /agent-workflows/{id}/nodes` | 可选 |
| 节点详情 | `GET /agent-workflows/{id}/nodes/{node_id}` | 可选 |

旧的 `/principals/resolve`、`/memory-retrievals` 不是当前 App Run 契约的一部分。
PA 的 `/api/v1/memory/search` 是独立遗留代理，不在本次账号与 LLM 调用范围内。

### 2.1 创建 Run

```http
POST /api/v1/app/runs
Authorization: Bearer <application credential>
X-Application-Id: personal_assistant
X-External-User-Id: 6f1c9a2e-...
Content-Type: application/json

{"user_request": "今天要带伞吗"}
```

```json
{
  "run_id": "0b1f...",
  "user_id": "remote-owner",
  "status": "pending",
  "user_request": "今天要带伞吗",
  "created_at": "2026-10-23T01:40:00Z"
}
```

`user_id` 是上游返回的远端所有者，PA **不存储、不返回**。当前 App Run 创建不接受
idempotency_key；重复提交由 PA 的 `(user_id, idempotency_key)` 唯一约束阻止。

### 2.2 启动工作流

当前 `AgentWorkflowCreate` 要求显式 general-agent 定义和 `role_bindings`：

```http
POST /api/v1/app/runs/{run_id}/agent-workflows
Accept: text/event-stream
```

```json
{
  "workflow_name": "general_agent",
  "workflow_version": "1.0.0",
  "execution_profile": "general_tools_v1",
  "idempotency_key": "ask-key:workflow",
  "role_bindings": {
    "controller": {"provider": "openai", "model": "gpt-4o-mini"}
  },
  "review_policy": "never",
  "stream": true
}
```

`role_bindings` 来自 `ASSISTANT_NEXUS_ROLE_BINDINGS_JSON`，是 assistant-api 的部署配置；
**不是**客户端字段。若 `review_policy != never`，还必须配置 `reviewer`。
API 返回 201 和 SSE 流；第一帧携带 `workflow_execution_id`。PA 在后台持续消费到终态，
POST /asks 只等到首个工作流帧即返回 202。

### 2.3 事件与结果

事件是已持久化的有限回放，不是无限实时订阅：

```text
id: 42
event: agent.workflow.completed
data: {"event_sequence":42,"event_type":"agent.workflow.completed",
       "workflow_execution_id":"...","run_id":"...","workflow_status":"completed",
       "public_payload":{"result_path":"/api/v1/app/agent-workflows/.../result"}}
```

- `Last-Event-ID: 42` 从 43 开始回放。
- 可取值：`pending/running/waiting_for_input/completed/failed/cancelled/outcome_unknown`。
- PA 映射：`completed → succeeded`、`outcome_unknown → unknown`，其余同名。
- 终态后 `GET /result` 返回 `final_output.final_text`；PA 只持久化该公开文本，不保存
  provider 原始响应、隐藏 reasoning 或远端 principal。
- 当前订阅接口不会在页面刷新后继续推送实时事件；客户端带 `Last-Event-ID` 重连，
  再通过 `GET /asks/{id}` 对账状态/最终回答。


## 3. 工具回调（NexusPilot → PA）

当前 `RemoteToolAdapter` 使用扁平 envelope，而不是旧的 `X-NexusPilot-*` 头：

```http
POST /internal/nexus/tools
Authorization: Bearer <service_token>
Content-Type: application/json

{
  "tool_execution_id": "call_...",
  "application_id": "personal_assistant",
  "external_user_id": "<PA user_id>",
  "tool_name": "personal_assistant.list_tasks",
  "tool_version": "1.0.0",
  "arguments": {"limit": 10}
}
```

- `external_user_id` 由 NexusPilot 从已持久化的 Run 写入，模型参数不能覆盖。
- 只接受已登记的只读工具：`personal_assistant.list_tasks`、`personal_assistant.search_notes`、
  `personal_assistant.list_calendar_events`；未知工具 404。
- 参数按 NexusPilot 静态 schema 校验：任务 `limit/cursor`，笔记 `query/limit/cursor`，
  日历 `start_date/end_date/limit/cursor`；任何 `user_id` 等额外字段 422。
- 返回 `{"items": [...], "next_cursor": null}`，`items` 最多 20 条，供 NexusPilot 的
  `BusinessReadResult` 校验。
- `tool_execution_id` 唯一；同一 ID 重放返回已存响应，不重复读取或产生业务影响。
  同一 ID 换用户/工具返回 403。

## 4. PA 对外 API（App 使用）

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/v1/asks` | 提交问题，202；重复 key 返回原记录 |
| GET | `/api/v1/asks` | 当前用户历史 |
| GET | `/api/v1/asks/{id}` | 状态、最终回答；必要时向上游对账 |
| POST | `/api/v1/asks/{id}/cancel` | 取消当前用户自己的运行 |
| GET | `/api/v1/asks/{id}/events` | SSE 代理，支持 `Last-Event-ID` |
| POST | `/api/v1/asks/verify` | 按 id 批量核对本地缓存是否属于聊天（2026-10-25 新增） |

`AskRead` 新增 `answer` 与 `completed_at`；`answer` 只来自终态 `final_output.final_text`。
状态 `pending/running/succeeded/failed/cancelled/unknown` 可区分；`unknown` 表示远端结果未知，
不自动重试。

### 4.1 聊天与内部运行分开（2026-10-25）

- `GET /api/v1/asks` 从 `user_asks` 出发查询：只有“用户在聊天里提出过这个问题”的运行
  才进入历史。内部任务（当前是通勤分析，后续可能更多）保存在 `nexus_runs`，
  但没有提问关联，因此不会出现在列表、详情、事件或取消接口里。
- 详情/事件/取消先校验当前用户的提问关联，缺失一律 404，且**不触达上游**。
  不能把 404 当作“删除本地记录”的依据：它同时代表不存在、不属于当前账号或已删除。
- `local_run_id`、`AskRead` 字段、`POST /asks` 路径与重放语义均未改变。
- 用途、业务关联、输入版本与触发方式由服务器根据已校验的业务记录写入，
  公开请求不能指定。聊天固定为 `purpose=chat`、`trigger=chat`。

### 4.2 本地缓存核对

```json
POST /api/v1/asks/verify
{"local_run_ids": ["<id>", "<id>"]}
→ {"results": [{"local_run_id": "<id>", "classification": "chat"}]}
```

- 1..50 个 id；只返回分类，不返回请求文本、提示词或模型输出。
- `chat`：存在当前账号的提问关联；`not_chat`：运行存在且属于当前账号，但为内部任务；
  `unknown`：查不到（不属于当前账号、已删除或迁移前无法判定）。
- 客户端只能据此清理 `not_chat`；`unknown` 必须保留并可重试。“没有出现在最近一页”
  只说明不在 `created_at` 前 N 条内，同样不是删除依据。

## 5. 错误与状态

- 上游 5xx/429 映射为可重试的 `nexus_unavailable`；超时为 `nexus_timeout` 且本地
  `status=unknown`，不得自动重试建 Run。
- 上游 4xx 映射为相应 `nexus_*` 错误；服务端配置导致的工作流 422 返回 502
  `nexus_invalid_request`（问题在本服务配置，不是 App 输入）。
- 所有 PA 错误仍为 `{error:{code,message,retryable},request_id}`。
- 取消只对当前用户本地记录可见；跨用户读/订阅/取消均 404，且不触达上游。

## 6. 已知边界

- 后台工作流观察器运行在 API 进程内；进程退出会中断实时观察，但工作流 id 和事件已持久化，
  刷新 `GET /asks/{id}` 会重新查询摘要并拉取最终结果。
- 当前上游 `/events` 是有限回放；PA 不伪造“断线后仍持续推送”。
- 上游 `result` 可能暂时 409 `workflow_not_completed`；PA 保留事件终态，后续读取再补结果。
