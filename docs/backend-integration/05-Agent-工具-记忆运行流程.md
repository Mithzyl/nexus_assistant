# 05 Agent、工具与业务记忆运行规则

> 更新：2026-11-10。以下当前规则与 backend/dev 一致；本Web分支原有2026-10-23 AgentProfile探索保留在后部，仅作历史，不作为需要补齐Nexus代码或新增平台表的任务。

## 当前职责与流程

Assistant 保存用户原文、可信身份、业务记忆和业务数据；Nexus 负责一个Agent内的模型上下文、工具意图和终答。Assistant默认检索一次自己的记忆，将必要事实和接口说明交给Nexus；模型提出调用后，Assistant校验权限/版本/参数一致性/重复请求并实际执行，原始调用、summary和结果回到同一Agent。

记忆由Assistant自己的数据库管理，Nexus平台记忆不参与正常业务写入。本人管理不要求先建立Run；AI修改必须绑定当前用户任务及原话。原文、助手改写、版本及纠正/回答来源分开保存。旧用途/角色设置和跨入口共享不是使用偏好的前提。

支持字段来源协议的独立日程与路线接口，由 AI 选择普通参数或 memory_defaults 之一，同字段重复提交（含相同值/null）写入前拒绝。其他已有业务工具也可由 AI 结合首轮记忆提出合法参数，11 月 10 日已实际创建/完成待办；不是记忆不能用于待办或通勤。后端本次已补待办、普通/通勤组合草稿、长期计划逐字段条目/版本/真实业务快照与明确重新应用的加密保存，7 项隔离定向通过；常用库新增 0029 与服务加载未执行。来源只用于内部核对，不新增前端来源入口，不默认在回答中铺开记忆正文；必要解释由 AI 决定，可用卡片。有歧义用 ask_user 卡片及下方选项，不要求新用途/角色设置。

影响本次业务结果的记忆读写在终答前执行；非紧急修改可在回答后队列中执行，queued不代表已经保存。Assistant worker处理该本地队列，不再调用模型。本机已完成 0028、独立密钥和旧记忆/历史导入；本会话 11 月 10 日最近一次核对为 run_backend.py 原生统一三进程，不据管理代码存在声称容器正在运行。本次未重查实时状态，其他环境不自动视为已启用。

backend/dev 已补齐参数/卡片格式修正提示，以及日程确认事务回滚后的conflict_detected/failed/continue。回调的result.data.error.details包含formal_write_status=not_applied、真实冲突、草稿及原目标，原Agent决定调整或询问，保留先前成功步骤；无回滚依据的错误、超时及提交后丢失响应仍unknown/stop。既有临时 SQLite 定向通过；11 月 10 日无冲突业务与格式纠正已有真实模型/浏览器证据，冲突条件未触发，不能计作真实冲突验收。详见[当前落地对照](../最新规划落地对照-现有能力与差距.md)和[开发安排](../开发安排.md)。本次未改Web业务代码，未调用模型。

## 当前应用与展示证据（2026-11-10）

同一隔离账号已有会议和报销习惯，AI 取得所需完整定义、查询会议，再创建一条 09:30 截止、09:20 提醒的待办，会议不变；后续新消息完成原待办。两条消息各对应一个 Agent，共 9 次调用，无提供商重试；原 Agent 两次修正卡片，没有重复写入。待办本次使用普通时间参数，不能称为待办逐字段 memory_defaults 或会议变化自动重算已实现。

Web 空数组展示问题已修复，执行回执不删，旧数据无列表仍兼容；保存响应的三项检查和 Chrome 重开历史通过，不再调用模型，提交 d976590 未推送。完整页面证据见[验收说明](../测试与验收说明.md#空卡片列表与旧执行回执2026-11-10)；后端当前覆盖依据实际 backend/dev 的同名 05 文档，不以本 Web 分支旧后端副本判断缺失。复杂路线/冲突、秒数新展示与目标通知仍未因此通过。

## 2026-10-23 AgentProfile探索（历史，不再作为实施要求）

> 原文中的“未实施/新增映射表/平台缺能力”等判断只描述当时设想。当前直接复用Nexus已有App/Agent能力，本规划不要求修改Nexus。以下不产生新的待办。

### 05 Agent / 工具 / 记忆运行流程

> 现状（2026-10-23）：下文 `application_agent_profiles` 表与 profile 解析尚未落地。
> 当前运行代码要求 `/api/v1/app/runs/{id}/agent-workflows` 的调用方提交
> `general_agent` + `role_bindings`。PA 从 `ASSISTANT_NEXUS_ROLE_BINDINGS_JSON`
> 生成这些字段，客户端不可见；这部分属于部署配置，不是 App 的 API。以下 profile
> 章节保留为未来方案。

#### 1. Agent Profile：把"选模型"收回服务端（未实施方案）

现状：`AgentWorkflowCreate`（`features/agent_runtime/schemas/agent_workflows.py:42`）要求 `role_bindings` 逐角色给出 `provider + model`，并且 `workflow_name`、`workflow_version`、`execution_profile` 三者有互斥校验。

让 PA 传这些字段有三个问题：PA 需要知道 provider 命名、PA 能绕过 `allowed_models`、换模型等于改 PA 代码。

新增一张映射表：

```sql
CREATE TABLE application_agent_profiles (
  application_id VARCHAR(64) NOT NULL,
  profile_name   VARCHAR(64) NOT NULL,
  status         VARCHAR(16) NOT NULL DEFAULT 'active',
  definition     JSON        NOT NULL,
  created_at     DATETIME(6) NOT NULL,
  updated_at     DATETIME(6) NOT NULL,
  PRIMARY KEY (application_id, profile_name),
  CONSTRAINT fk_profile_application
    FOREIGN KEY (application_id) REFERENCES applications(application_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`definition` 的结构与现有请求体同构，便于直接校验：

```json
{
  "workflow_name": "general_agent",
  "workflow_version": "1.0.0",
  "execution_profile": "general_tools_v1",
  "review_policy": "always",
  "role_bindings": {
    "controller": {"provider": "deepseek", "model": "deepseek-chat"},
    "reviewer":   {"provider": "deepseek", "model": "deepseek-chat"}
  },
  "max_nodes": 16,
  "wall_time_limit_ms": 300000
}
```

解析顺序（服务端）：

1. 按 `(application_id, profile_name)` 取定义；不存在 → 404 `profile_not_found`。
2. 用 `allowed_models` 过滤 `role_bindings`；若某角色模型不在白名单 → 403 `model_not_allowed`，**不做静默降级**。
3. 用 `allowed_tools` 过滤工具集。
4. 把 PA 传来的运营参数（`stream`、`max_nodes`、`wall_time_limit_ms`）与定义取更严格的一方（取 min），不允许调用方放大上限。
5. 组装成 `AgentWorkflowCreate` 后交给既有 `AgentWorkflowExecutionService`。

PA 侧当前发送 `workflow_name=general_agent`、`workflow_version=1.0.0`、
`execution_profile=general_tools_v1`、`stream=true` 和由服务端配置生成的 `role_bindings`；
`agent_profile` 已不存在。

#### 2. 一次运行的完整流程

```text
POST /app/runs
  └─ 校验应用 + 主体 + allowed_models + 预算
  └─ 写 llm_runs（user_id = platform_user_id, run_type='assistant'）
POST /app/runs/{id}/agent-workflows
  └─ profile 解析 → AgentWorkflowCreate
  └─ llm_agent_workflow_executions（幂等键）
  └─ 进程内执行任务启动（workflow_execution_tasks）
       ├─ Controller 规划
       ├─ 工作节点（可并行 2 个）
       │    └─ 内层工具循环 agent_tool_loop
       ├─ 确定性验证
       ├─ Reviewer 审核
       └─ 汇总
  └─ 每步写 llm_agent_workflow_events（event_sequence 递增）
GET /app/agent-workflows/{id}/events
  └─ 只回放已提交事件，按 Last-Event-ID 续传
```

不改变现有语义：

- 执行仍在 API 进程内，进程崩溃不恢复（`workflow_execution_tasks.py` 已明确）。
- Reviewer 与内层工具循环保持不变，PA 不能关闭审核。
- 预算按 Run 记账，PA 传入的 `budget_limit` 只能收紧。

#### 3. 工具：注册、绑定、回调

##### 3.1 注册

复用既有 `RegisteredTool`（`services/tool_catalog.py:15`）。它已经强制：名称正则、语义化版本、Pydantic 输入输出、`required_capabilities`、风险级别、超时、结果字节上限。

需要扩展两处：

| 扩展 | 原因 |
|---|---|
| `risk_level` 增加 `application_write` | 现有只有 `read_only` / `task_write`，无法表达"会改外部系统" |
| 新增 `ToolTransport` 概念（`builtin` / `application_http`） | 现有工具服务直接在进程内读本服务数据 |

##### 3.2 绑定

```sql
CREATE TABLE application_tools (
  application_id   VARCHAR(64)  NOT NULL,
  tool_name        VARCHAR(64)  NOT NULL,
  tool_version     VARCHAR(16)  NOT NULL,
  transport        VARCHAR(24)  NOT NULL,     -- application_http
  endpoint_url     VARCHAR(512) NOT NULL,
  credential_ref   VARCHAR(255) NOT NULL,     -- 指向密钥存储，不存明文
  risk_level       VARCHAR(24)  NOT NULL,
  timeout_seconds  INT          NOT NULL,
  max_result_bytes INT          NOT NULL,
  status           VARCHAR(16)  NOT NULL DEFAULT 'active',
  PRIMARY KEY (application_id, tool_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`endpoint_url` 必须命中应用注册时声明的域名白名单，禁止任意 URL（否则模型能借工具回调打内网）。

##### 3.3 执行

新增 `ApplicationToolService`，结构照 `AgentFileToolService`（`services/agent_file_tool_service.py:231`）：

```python
async def execute(self, *, attempt_id, call, offered_versions) -> LlmModelToolCall:
    """先落库认领，再出网；结果单独持久化，重复调用直接拒绝。"""
```

顺序固定为：

1. `llm_tool_calls` 认领（provider call id 唯一，重复即 409）。
2. 校验版本在白名单、`required_capabilities` 满足。
3. 从 Run → principal → mapping 取 `external_user_id`。
4. 出网调用 PA，带四项头（应用令牌、external user、tool call id、时间戳）。
5. 结果先写 `llm_tool_calls`，再回给模型。

**模型无法决定 external user。** 它只产出参数；第 3 步是服务端反查。

##### 3.4 写操作

`risk_level='application_write'` 的工具必须满足：

- 进入 `review_policy != "never"` 的工作流；
- 工具返回必须带 `idempotency_key`，由 NexusPilot 生成并透传给 PA；
- PA 侧对同一 key 返回同一结果，不重复创建；
- 超时视为**结果未知**，返回 `status="unknown"`，禁止自动重试。

建议第一批只上只读工具（`list_user_tasks`、`search_user_notes`、`read_user_note`、`get_calendar_events`），写工具等审核闸门与对账接口完成后再开。

#### 4. 记忆

现状：`llm_memories`、`llm_memory_versions`、`llm_memory_retrievals` 等表已存在，检索服务有版本化评分与来源，但 README 明确其**未接入** `/responses` 与工作流，属于暂停项。

##### 4.1 第一阶段：只读召回

```text
用户消息
  → 是否已有会话上下文？有则跳过初始召回
  → 否：POST /app/memory-retrievals（query=当前消息，limit≤10）
  → 把命中的 content + source_reference 作为"参考"注入，不当作事实断言
  → Agent 需要更多时自行调用 search_memory 工具
```

硬性约束：

- 召回失败 → 返回 5xx，不返回空列表（空列表表示"没有"）。
- 召回结果必须带 `source_reference` 才能进入答案；无来源的记忆不得作为事实陈述。
- 每次召回有 `idempotency_key`，重试不重复计费。

##### 4.2 第二阶段：对话后异步提取

```text
Run 完成
  → 出站事件（llm_outbox_events 已有表）
  → 后台任务：读取本次消息 → 生成候选记忆
  → 写入 llm_memories(status='candidate')
  → 用户确认或规则通过 → status='active'
```

**不把运行状态写进记忆。** "本次 Run 正在等用户确认"是执行状态，不是长期偏好。

##### 4.3 隔离

记忆按 `user_id` 作用域，而应用用户各自拥有独立 platform user，因此跨应用天然隔离，**不需要给记忆表加 `application_id`**。

未来若要跨应用共享，前置条件是明确的主体关联与用户授权，不能靠"同一个 external_user_id 字符串"推断同一个人。

#### 5. 缓存缺位的处理

仓库没有 Redis，因此：

| 需求 | 第一阶段做法 |
|---|---|
| 工具调用去重 | MySQL 唯一键（`llm_tool_calls.provider_tool_call_id`、PA 侧 `tool_call_receipts.tool_call_id`） |
| 幂等重放 | MySQL 唯一键 + 读回，不用缓存 |
| 登录失败计数 | 已有 `authentication_audit_events` 表，按窗口聚合查询 |
| SSE 广播 | 不做进程间广播；同一进程内靠队列，跨进程靠事件回放 |
| 应用令牌校验 | 每次查库（可加 60 秒进程内 LRU，但撤销会延迟，需权衡） |

若后续引入 Redis，必须先明确"缓存失效后是否仍正确"，而不是先加缓存再补一致性。
