# 第一轮联合验收记录（acceptance/round1）

> **本文是 2026-10-25 第一轮联合验收的历史证据，不是当前验收标准，也不是最新版本结果。**
> 当前验收要求以 [`产品规划-工作安排与计划接续.md`](产品规划-工作安排与计划接续.md) 第三部分「核心验收清单」六条为准，逐条对照见 [`测试与验收说明.md`](测试与验收说明.md)。文中的测试数量、合并记录和修复结果只对当时范围有效，不能替代现行六条验收。

临时分支 `acceptance/round1`，工作目录
`/Users/mith/Desktop/project/superapp-next-worktrees/acceptance`。
`main` 与三个开发分支在本轮都没有改动或推送。

## 1. 纳入的提交

| 来源分支 | 提交 | 内容 |
|---|---|---|
| `backend/dev` | `259640a4` | 运行状态区分服务器响应 / 进程心跳 / 任务进度 |
| `backend/dev` | `22ade90e` | 隔离运行与主库状态文档 |
| `backend/dev` | `dea4d1a4` | 契约检查改用已提交客户端字段 |
| `backend/dev` | `6dd07ccd` | 补查文档（聊天核对、原库、两套环境） |
| `ios/dev` | `95268e10` | 聊天核对、独立天气、真实检查阶段 |
| `web/dev` | `e0a92b8b` | 旧聊天缓存分类、天气分离、检查阶段 |

合并顺序：backend → ios → web，各用 `git merge --no-ff`：

- `dcf85730` Merge branch 'backend/dev'
- `56910a9e` Merge branch 'ios/dev'
- `48b7884f` Merge branch 'web/dev'

**冲突：0 处。** 三条线改动的文件互不重叠（13 / 19 / 18 个文件，无交集），
所以没有需要人工取舍的文本冲突。合并后的语义一致性由下面的检查验证，而不是靠
“没有冲突”下结论。

## 2. 联合验收发现并修复的集成缺陷

**现象**：真实后台流程执行成功后，`GET /api/v1/advisories/{id}` 返回 **500**；
网页与 iOS 都无法显示后端已经保存的建议。

**原因**：worker 在 AI 运行映射无法确定时，把 `ai_analysis_json.status` 写成
`unknown`（`services/.../commute/services/ai.py:_unknown`），但
`AiAnalysisRead.status` 的 Literal 只有 `available/unavailable/rejected`，
序列化时 Pydantic 校验失败。Swift 的 `AiAnalysis.Status` 同样没有 `unknown`，
即使后端放行，iOS 解码也会失败。

**范围**：该字符集在基线 `ca2b44fb` 就已经是三值，属于合并后才被真实流程暴露的
兼容性缺陷，不是本轮三条分支引入的。

**修复**（三端同时补齐，保持语义而不是掩盖状态）：

| 文件 | 改动 |
|---|---|
| `services/assistant-api/src/assistant_api/commute/schemas.py` | `AiAnalysisRead.status` 增加 `unknown` |
| `apps/web/lib/mobile/api.ts` | AI 分析状态联合类型增加 `unknown` |
| `ios/.../Commute/CommuteModels.swift` | `AiAnalysis.Status` 增加 `case unknown` 与文案 |
| `ios/.../Commute/CommutePresentation.swift` | 穷举 switch 增加 unknown 分支 |
| `services/assistant-api/tests/test_backend_operations.py` | 回归测试：unknown 可被读模型接受 |
| `docs/commute/openapi.json` | 重新导出（32 路径） |

### 2.1 第二轮发现：AI 观察器引用两个不存在的函数

**现象**：模拟模型返回合法结果时，建议的 `ai_analysis.status` 仍是 `unknown`，
`nexus_runs.error_code` 为 `nexus_protocol_error`，客户端永远看不到“模型分析可用”。

**原因**：`run_service._consume_workflow` 调用 `_sequence_of_event(event.id)` 和
`_event_error_code(data)`，但这两个函数在模块里**根本不存在**（基线 `ca2b44fb`
同样缺失）。异常被后台观察器的兜底 `except Exception` 吞掉，只留下
`nexus_protocol_error`，所以看起来像“上游返回未知”。

**影响**：所有 AI 运行（包括通勤内部任务与聊天）都会以 unknown 结束；“模型返回
有效建议”这条路径在本轮之前从未真正跑通过。

**修复**：

| 文件 | 改动 |
|---|---|
| `services/assistant-api/src/assistant_api/services/run_service.py` | 补 `_sequence_of_event`（数字 id → int，容忍缺失/非数字）、`_event_error_code`（读取嵌套 error/public_payload，仅返回有界字符串）；给被吞的异常加 `logger.exception` |
| `services/assistant-api/tests/test_run_service_helpers.py`（新） | 4 条回归测试：数字/opaque id、嵌套 error code、缺失值 |

修复后用模拟模型跑通了 `available` 与 `unknown` 两条流程（见 5.5）。

## 3. 契约覆盖补充

`check_contract.py` 原来只覆盖 11 接口 / 19 路由，把客户端已经在调用的三条路由列为未覆盖。
本轮按“只补清单、不改产品”的方式加入：

| 接口 | 证据 |
|---|---|
| `AskVerifyResponse` | `apps/web/lib/mobile/api.ts`、`ios/.../PA/PAModels.swift` |
| `PlanWeatherRead` | `apps/web/lib/mobile/api.ts`、`ios/.../CommuteModels.swift` |
| `RuntimeRead`（仅 `route_provider_available`） | `apps/web/.../me/commute/[id]/page.tsx` |

结果：`contract ok: 14 interfaces, 22 routes, 6 interfaces with additive fields`。
仍未覆盖：内容/来源同步、`/memory/search`、`/health`、`/internal/nexus/tools**`。
iOS 目前没有 runtime 模型，因此没有假装覆盖。

## 4. 环境与代码路径（可复核）

- 后端代码：`acceptance/services/assistant-api`；
  解释器 `.tmp/backend-venv/bin/python`（Python 3.11.9，`pip install -e "services/assistant-api[dev]"`）；
  运行时 `assistant_api.__file__` =
  `.../acceptance/services/assistant-api/src/assistant_api/__init__.py`。
- 网页代码：`acceptance/apps/web`（生产构建，`next start -p 3310`）。
- 数据库：`.tmp/acceptance-flow/assistant.db`（每次运行重建，不使用共享库）。
- 端口：API 8310、网页 3310、Nexus 桩 8320；共享 8010 未启动。
- 上游：`services/nexus-stub`（内存桩，模拟 Nexus 契约）；天气/路线 `mock`；
  自动模型分析与自动天气刷新关闭；通知不发送。

## 5. 测试结果

| 项目 | 命令 | 结果 |
|---|---|---|
| 后端单元/接口 | `.venv/bin/python -m pytest -q` | **180 passed**（176 + 4 条观察器助手回归） |
| 客户端契约 | `scripts/check_contract.py` | **ok：14 接口 / 22 路由** |
| 网页类型 | `pnpm --filter @superapp/web typecheck` | 通过 |
| 网页构建 + 浏览器 | `pnpm --filter @superapp/web test:e2e` | **90/90 checks passed**（隔离库、固定时钟、Playwright） |
| iOS 构建 | `xcodebuild ... -destination 'platform=iOS Simulator'` | **BUILD SUCCEEDED** |
| iOS 单元测试 | `xcodebuild test ... -only-testing:LifeAssistantTests` | **124/124 通过**（默认签名）。注意：加 `CODE_SIGNING_ALLOWED=NO` 会去掉 Keychain 权限，导致 4 条 Keychain 用例假失败 |
| iOS 新界面测试 | `-only-testing:LifeAssistantUITests/StateExpressionUITests` | **3/3 通过**（天气分离、未知聊天核对、等待状态只显示已确认阶段） |
| 真实后台流程 + 跨端 | `tests/e2e/acceptance_round1_flow.py` | **29/29 passed** |
| 模拟器连隔离后端 | `-only-testing:LifeAssistantUITests/LiveBackendUITests` | **1/1 通过**（97 秒，见 5.4） |
| 两条模型结果 + 心跳 | `tests/e2e/acceptance_model_flows.py` | **23/23 passed**（见 5.5） |

### 5.1 真实后台执行流程（不是预置状态）

`tests/e2e/acceptance_round1_flow.py` 在本目录启动真实 API、真实 worker、真实生产构建网页：

1. 合成账号 `acceptance-a@example.com`（仅临时库）创建计划（今天 23:00 到达、21:00 检查）。
2. 网页点击“重新分析” → 数据库出现 **1 条 `queued`** 检查（客户端提交，非脚本插入）。
3. `worker --once` 领取并推进：`queued → running → succeeded`，`phase=done`。
4. 上游用 Nexus 桩返回结果；后端保存建议，`GET /checks/{id}` 返回该建议的 `advisory_id`，
   `GET /advisories/{id}` 按 id 读取成功（修复前此步 500）。
5. 网页进入“已完成”，客户端保存的 `checkId` 与数据库一致。
6. 关闭页面后重新打开：同一 `checkId`，`commute_checks` 仍为 1，`commute_advisories` 仍为 1，
   `commute_internal` 模型运行仍为 1（没有重复创建）。

### 5.2 跨端场景

| 场景 | 验证方式 | 结果 |
|---|---|---|
| 同一账号两端读同一条聊天 | API 返回 `local_run_id` + 网页 `/ask` 显示同一文本；后端 `user_asks=1` | 通过 |
| 内部通勤运行在聊天页不可见 | `/asks` 列表只含聊天，无通勤提示词；`nexus_runs.purpose=commute_internal` 不进入列表 | 通过 |
| 天气单独更新不改旧建议 | 模拟新的 provider 天气写入后，`/weather` 可读，旧建议 `weather_json`/`generated_at` 不变 | 通过 |
| 账号切换隔离 | 账号 B 的计划与聊天均为空，身份为 B | 通过 |

### 5.3 明确区分

- **预置状态的页面测试**：`web_flow_check.py`（90 项）用 fixture 直接构造阶段/历史，
  证明渲染与文案；它**不**证明后台真的推进。
- **真实后台推进**：`acceptance_round1_flow.py` 的 29 项，使用真实进程与真实库。
- **模拟上游**：Nexus 桩、mock 天气/路线；**没有**验证真实 Nexus、WeatherKit/Maps、
  APNs/Bark 送达。
- iOS 界面只跑了不依赖后端的三条 StateExpression 用例；`LoginPersistenceUITests`、
  `CloudSyncUITests`、`NoteImportIsolationUITests`、`PAEndToEndUITests` 需要 8010 上的
  共同后端，按本轮约定没有启动，因此**未验证**（其中 AskChatOrder 一条通过）。
- 没有真机验收，全部为 iPhone 18 Pro 模拟器。

### 5.4 苹果客户端在模拟器里连真实隔离后端

不是“接口请求 + 网页显示”，而是 XCUITest 驱动模拟器里的 App：

- 后端：`tests/e2e/live_backend_fixture.py` 在 `127.0.0.1:8410` 启动合并后的 API，
  库是 `.tmp/live-ios/assistant.db`，两个合成账号各有一条计划与一条**可用**的模型建议；
  上游仍为 Nexus 桩（`STUB_FINAL_TEXT` 返回结构化结果），没有真实模型/天气/通知。
- App 通过 `PA_API_BASE_URL=http://127.0.0.1:8410` 指向该实例，未使用 `PA_DEMO`。
- `LiveBackendUITests` 依次验证：账号 A 登录 → 今天页出现服务器上的“甲的通勤计划”
  与“AI 可用” → 终止并重启后会话恢复、数据仍在 → 退出并登录账号 B →
  今天页出现“乙的通勤计划”，且**不再出现**“甲的通勤计划”→ 三步各留截图。

结果：`Executed 1 test, with 0 failures` / `TEST SUCCEEDED`（97.3 秒）。

### 5.5 后台心跳与两条模型结果流程

`tests/e2e/acceptance_model_flows.py`（真实 API + 真实 worker + 生产构建网页，23/23）：

| 结论 | 证据 |
|---|---|
| 后端确实用 `service_heartbeats.last_seen_at` 判定执行程序是否在线 | 未跑 worker 时 `worker.state=not_seen`；`worker --once` 后 `state=online` 且带 `last_seen_at`；状态由 `stale_seconds` 与时钟差计算（`commute/heartbeat.py:123-136`） |
| 网页用的是心跳，不是路线可用性或任务阶段 | 未跑 worker 时页面显示“尚未发现后台执行程序”；worker 后显示“后台执行程序在线”；同一时刻 `route_provider_available=true`、检查阶段仍在 `queued`，三者互不替代 |
| 苹果客户端**没有**用心跳 | iOS 没有调用 `/commute/runtime`，也没有 runtime 模型；`CommuteStore` 只用 `check.phase`/`status` 文案（`CommuteStore.swift:103-113`）。因此 iOS 只能显示“已提交，等待处理”或真实阶段，**无法**区分“执行程序离线”与“排队中”。这是待补缺口，本轮未改代码 |
| 模型返回有效建议（模拟） | 客户端提交 → worker → `ai_analysis.status=available`、`used_duration_seconds=1800`、建议标题来自模型 JSON；`GET /advisories/{id}` 200；网页显示模型标题与“模型分析（基于开发/测试数据）”标签 |
| 模型结果未知（模拟） | `/_debug/unknown_once` 让下一次工作流 `outcome_unknown` → 建议 `ai_analysis.status=unknown`，仍可读取（不 500）；客户端按该检查的 `advisory_id` 显示“模型分析未完成” |

## 6. 失败与修复过程

| 现象 | 原因 | 处理 |
|---|---|---|
| 首次运行点击“重新分析”后无检查 | 计划到达时间 09:00 已过，接口返回 `check_window_closed`（客户端如实提示） | 计划改为 23:00 到达、21:00 检查 |
| `/advisories/{id}` 500 | `unknown` 状态不被读模型接受 | 三端补齐 + 回归测试 + 重导 OpenAPI |
| 契约检查 175→176 期间失败 | 测试断言仍要求三条路由“未覆盖” | 断言改为“已覆盖”，并保留 `/health` 仍未覆盖的检查 |
| 首次页面重开时读到 `None` 检查 | Playwright `browser.new_page()` 新建了隔离上下文，丢了 localStorage/Cookie | 改为同一 context 内 `new_page()` |
| worker 未推进 | 队列为空（提交未成功） | 先确认库内存在 queued 再跑 worker |
| 模拟模型总是 `unknown`/`nexus_protocol_error` | 观察器调用了不存在的 `_sequence_of_event`/`_event_error_code`，异常被吞 | 补齐两个函数 + 4 条回归测试 |
| 点击“重新分析”偶发不建检查 | 页面计划尚未加载完就点击，`startAnalysis` 直接返回 | 等待计划标题出现，并对提交做有界重试 |
| 网页看不到 unknown 文案 | 重载后客户端只读自己那次检查的 `advisory_id`，而未知流程是脚本用 API 造的检查 | 第二条检查改由客户端提交，服务端再返回 unknown |

## 7. 剩余问题（不要当成已验证）

1. 依赖 8010 共同后端的其他 iOS UI 用例（登录持久化 8010、云同步、账号切换晚到响应）
   仍未跑；本轮新增的 `LiveBackendUITests` 已用 8410 验证读取/恢复/切换，
   但不覆盖那些用例的全部断言。
2. **iOS 没有接后台心跳**：只能显示检查阶段/状态，无法提示“执行程序离线”。
   网页已具备该能力。这是本轮查清的缺口，未擅自改产品代码。
3. 聊天、天气、进度在 iOS 上仍以编译 + 单元测试 + 后端契约为主；模拟器只验证了
   通勤读取与账号隔离。
3. 真实 Nexus、真实天气/路线、Bark/APNs 送达未验证；自动分析仍默认关闭。
4. 共享 8010 未启动；原用户数据库仍未找到，未做任何迁移或恢复。
5. 三个验收脚本依赖本目录的 `.tmp/backend-venv`、空闲端口与 Playwright，未纳入 CI。
6. iOS 真机未验收，全部为 iPhone 18 Pro 模拟器。

## 8. 建议合入 main 的内容（任务清单已删除）

> 2026-10-26 清理：本节原有“建议合入 main”的行动清单属于第一轮验收的后续安排，已过时，条目已删除。第 1、2、5、6 节的提交、缺陷与修复证据保留，不因删除行动清单而失效。当前开发与验收见 [`开发安排.md`](开发安排.md) 与 [`测试与验收说明.md`](测试与验收说明.md)。
