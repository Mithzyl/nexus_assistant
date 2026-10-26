# Personal Assistant × NexusPilot 后端集成设计

> 本文是 2026-10-23 至 2026-10-25 的后端集成设计与联调记录，不是当前产品规划。当前规划见 [`../产品规划-工作安排与计划接续.md`](../产品规划-工作安排与计划接续.md)，能力差距见 [`../最新规划落地对照-现有能力与差距.md`](../最新规划落地对照-现有能力与差距.md)。

对齐对象：`/Users/mith/Desktop/project/llm_fusion_platform`（NexusPilot，版本 0.8.0，分支 `codex/general-agent-tool-loop`）。
本文所有"已有/没有"的判断都来自该仓库当前代码，未采用路线图里的计划性描述。

## 结论摘要

1. **AI 执行内核已经存在，缺的是"应用"这一层身份。** Run/Task/Attempt、Agent 工作流、工具目录与内层循环、SSE 事件回放、取消、Memory 检索都已实现；但 `grep -r "application_id"` 在整个 `apps/` 与 `packages/` 零命中，93 张 `__tablename__` 中没有一张属于应用、主体或映射。
2. **最小改动是加一层主体，而不是重写调用链。** 认证后把 principal 对应的平台用户写入既有的 `db.info["authorized_user_id"]`，后续 `authorize_resource_request`、`owned_list_statement`、`require_resource_owner` 与所有 service 都不需要改。这是本设计最重要的复用点。
3. **两个现有 DTO 与"禁止客户端声明用户"直接冲突。** `RunCreate.user_id`（`apps/api/src/nexuspilot_api/schemas/runs.py:15`）与 `MemoryRetrievalCreate.user_id`（`features/memory/schemas/memories.py:268`）都要求调用方传用户。Personal Assistant 不能使用这两个 DTO，需新增应用专用 schema，用户从凭据派生。
4. **模型选择是服务端部署配置。** 当前 `AgentWorkflowCreate.role_bindings`
   （`features/agent_runtime/schemas/agent_workflows.py:42`）要求逐角色给
   `provider + model`。PA 通过 `ASSISTANT_NEXUS_ROLE_BINDINGS_JSON` 提供，客户端不可见。
   上游执行和模型凭据仍归 NexusPilot。
5. **仓库没有 Redis / Dragonfly。** `apps/api/requirements.txt` 与 `deployments/compose/docker-compose.yml` 内只有 MySQL 与 MinIO。第一阶段的幂等、去重、冷却必须落在 MySQL 唯一约束上，不能假定已有共享缓存。
6. **Memory 仍是实验性且被暂停。** README 明确"Memory 当前只保留规划和实验性准备代码……不进入实际 `/responses` 或 Agent 工作流调用链"。第一阶段只做"经 API 的只读召回"，不做自动写入，也不把它当业务事实。

## 交付物

| 文件 | 内容 |
|---|---|
| [`01-技术栈与复用边界.md`](01-技术栈与复用边界.md) | 两个项目真实依赖对照，逐项标注已有 / 需新增 / 可直接复用 |
| [`02-架构与调用链.md`](02-架构与调用链.md) | 系统架构、四条调用链、数据所有权边界 |
| [`03-应用身份与用户映射.md`](03-应用身份与用户映射.md) | 当前 Application / Mapping / Run context 模型与 PA 侧身份做法 |
| [`04-API-SSE契约.md`](04-API-SSE契约.md) | `/api/v1/app/*` 端点、请求响应、错误码、SSE 事件与断线恢复 |
| [`05-Agent-工具-记忆运行流程.md`](05-Agent-工具-记忆运行流程.md) | Agent Profile 映射、工具注册与回调、Memory 三段式召回 |
| [`06-改动清单与验证.md`](06-改动清单与验证.md) | NexusPilot 侧逐文件改动、迁移、测试清单、已完成的 PA 代码与实测结果、阻塞项 |
| [`07-前后端接口协商.md`](07-前后端接口协商.md) | 23 页前端 → 后端能力矩阵、字段级决议、待建资源与端点、需要拍板的四件事 |
| [`09-当前AppAPI联调结果.md`](09-当前AppAPI联调结果.md) | 当前 NexusPilot App API 对账、修复文件、测试结果与真实环境未验证项 |
| [`10-真实联调记录-20261023.md`](10-真实联调记录-20261023.md) | 真实 NexusPilot/DeepSeek 联调、402 阻塞、工具格式与隔离验证 |
| [`12-设备内容同步契约.md`](12-设备内容同步契约.md) | 设备来源、字段/助手授权、范围同步、内容查询与工具适配 |
| [`14-智能通勤分析契约.md`](14-智能通勤分析契约.md) | checks 路线快照、AI 分析状态、幂等和失败原因 |

## 已交付的可运行代码

`services/assistant-api/`（本项目内，非 NexusPilot 仓库）：

```text
services/assistant-api/
├── pyproject.toml
├── src/assistant_api/
│   ├── core/            # 配置、错误码、密码/令牌、依赖
│   ├── models/          # users / profiles / sessions / nexus_identity_links
│   │                    # tasks / schedules / notes / business_records
│   │                    # nexus_runs / tool_call_receipts
│   ├── nexus_client/    # current App API client / models / errors / SSE
│   ├── services/        # run / tool / memory
│   ├── commands/        # 本地建号与示例数据
│   └── routers/         # auth / assistant / internal_tools
├── scripts/             # e2e_check.py / check_contract.py
└── tests/               # 98 个契约、隔离与进程内回归测试

services/nexus-stub/     # NexusPilot 应用 API 的可执行桩（真实 HTTP）
v3-kit/source/src/api/contract.ts   # 前端类型与端点常量
```

实测：
- `pytest -q` → `98 passed`（离线，SQLite + `httpx.MockTransport`）
- `scripts/e2e_check.py` → `15/15 checks passed`（当前契约的真实 HTTP 进程，含 SSE 与续传）
- `scripts/check_contract.py` → `contract ok: 7 interfaces, 9 endpoint constants`
- `tsc --noEmit` → 通过

## 边界与未完成

- **NexusPilot 仓库本次全程只读**，未新增、未修改、未删除任何文件。第 06 篇给出的是待实施清单，不是已落地代码。
- 因此"PA 与真实 NexusPilot 端到端"没有跑过。已跑通的是 PA 侧契约，以及用当前契约桩验证的协议行为。
- `services/assistant-api` 是设计验证脚手架：SQLite 建表由 `allow_schema_create` 控制，未接 Alembic；未做限流、审计、后台任务、部署编排。
- 脚手架安装的是 FastAPI 0.161.1，NexusPilot 锁定 0.116.1。接入前应统一版本，见第 06 篇"待对齐"。
- Memory 自动提取、写入审核、跨应用共享均未实现，也不建议在第一阶段做。

## 建议阅读顺序

先读 03（身份是全部设计的前提）→ 04（接口契约）→ 05（运行流程）→ 02（串起来看）→ 06（落地顺序）。
只想知道"要改哪些文件"的，直接跳到 06。
