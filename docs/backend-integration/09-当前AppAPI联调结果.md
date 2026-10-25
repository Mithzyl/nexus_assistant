# 09 当前 App API 联调结果

日期：2026-10-23。目标是最小可用：App 本地账号登录后提交问题、查看状态与最终回答、
订阅事件、刷新恢复、取消自己的运行。模型执行仍在 NexusPilot。

## 1. 只读核对到的当前接口

参考代码（全程只读）：
`apps/api/src/nexuspilot_api/features/identity/routers/application_runs.py`、
`features/agent_runtime/routers/agent_workflows.py`、
`features/agent_runtime/schemas/agent_workflows.py`、
`services/application_tools.py`。

| 项目 | 当前实际 | 旧设计（已废弃） |
|---|---|---|
| 应用标识 | `X-Application-Id` | `X-NexusPilot-Application` |
| 用户断言 | `X-External-User-Id` | `X-NexusPilot-External-User` |
| 用户解析 | 认证应用时自动解析/创建 mapping | `POST /principals/resolve` |
| 创建 Run | body 仅 `user_request`；201 | 带 run_type/idempotency_key 等 |
| 启动工作流 | `general_agent` + `1.0.0` + `general_tools_v1` + `role_bindings`，`stream=true` 返回 SSE | `agent_profile` + 非流式 JSON |
| 事件 | `GET /agent-workflows/{id}/events`，已持久化事件有限回放 | 假设可持续实时推送 |
| 结果 | `GET /agent-workflows/{id}/result`，`final_output.final_text` | 事件内直接携带 answer |
| 取消 | `POST /agent-workflows/{id}/cancel` | 同路径 |

`role_bindings` 是当前上游必填项。PA 用 `ASSISTANT_NEXUS_ROLE_BINDINGS_JSON` 提供，
属于服务端部署配置；浏览器和模型都看不到。上游仍负责实际模型执行与凭据。

## 2. 本次修改

- 客户端改为当前 `X-Application-Id` / `X-External-User-Id`，删除 principal resolve。
- 创建 Run 的 body 收窄为 `{"user_request": ...}`。
- 工作流改为当前 general_agent 定义与流式启动；PA 在后台消费到终态。
- `AskRead` 增加 `answer`、`completed_at`；`GET /asks/{id}` 会从上游摘要/结果对账。
- 取消改为当前工作流取消路径；本地取消是粘性的，迟到的成功流不会复活已取消运行。
- `nexus_runs.nexus_principal_id` 改为可空，由 SQLite 迁移器维护。
- 删除已失效的 `services/mapping_service.py` 与 principal 相关模型/客户端代码。
- 更新 `.env.example`、04/03/02/05/README 等直接相关文档；App 侧 TS `AskRead` 增加字段。

## 3. 测试结果

- `pytest -q`：**98 passed**
- `scripts/e2e_check.py`：**15/15 passed**（真实 HTTP 两进程，当前契约 stub）
- `scripts/check_contract.py`：ok（7 interfaces / 9 endpoints）
- OpenAPI 导出：19 paths，已刷新 `docs/commute/openapi.json`
- 新增回归覆盖：当前请求头/body、无 principal resolve、SSE 订阅与续传、最终回答持久化、
  刷新恢复、取消、重复提交、上游失败、跨用户读/订阅/取消、会话过期，以及当前工具
  envelope、结果格式、用户隔离和调用 ID 幂等。

## 4. 真实 NexusPilot 联调状态

已完成：

- 真实 `/api/v1/app` 认证、application mapping 自动创建、RunApplicationContext 校验。
- 真实 Run 创建、工作流启动、SSE 消费和 failed 状态持久化。
- 失败运行的事件回放与 `Last-Event-ID` 续传；取消后刷新保持 `cancelled`。
- 真实 NexusPilot `RemoteToolAdapter` 调用 PA 只读工具：结果通过
  `BusinessReadResult` 校验，用户 A/B 数据隔离，重复调用 ID 返回缓存。
- 用户 B 读取/订阅/取消用户 A 的 PA Run 均 404。

未完成：

- 真实 DeepSeek 返回 `HTTP 402 Insufficient Balance`（A/B 两次都是
  `failed / invalid_request`，0 token），因此没有最终回答，也没有模型触发的工具调用。
  需要充值或替换 NexusPilot 的 DeepSeek key 后重跑。详见
  [`10-真实联调记录-20261023.md`](10-真实联调记录-20261023.md)。
- 当前上游事件接口是有限回放；跨进程实时 fan-out 未实现，App 需要重连/对账。
- 未部署、未提交、未推送。
