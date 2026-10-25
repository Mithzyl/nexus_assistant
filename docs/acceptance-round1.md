# 第一轮联合验收记录（acceptance/round1）

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
| 后端单元/接口 | `.venv/bin/python -m pytest -q` | **176 passed** |
| 客户端契约 | `scripts/check_contract.py` | **ok：14 接口 / 22 路由** |
| 网页类型 | `pnpm --filter @superapp/web typecheck` | 通过 |
| 网页构建 + 浏览器 | `pnpm --filter @superapp/web test:e2e` | **90/90 checks passed**（隔离库、固定时钟、Playwright） |
| iOS 构建 | `xcodebuild ... -destination 'platform=iOS Simulator'` | **BUILD SUCCEEDED** |
| iOS 单元测试 | `xcodebuild test ... -only-testing:LifeAssistantTests` | **阻塞**：`Pseudo Terminal Setup Error / Operation not permitted`，测试运行器无法启动（本机沙箱限制，非编译失败） |
| 真实后台流程 + 跨端 | `tests/e2e/acceptance_round1_flow.py` | **29/29 passed** |

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
- iOS 界面未在真机/模拟器上与真实后端联调：仅编译通过 + 既有单元测试（本轮因环境阻塞未跑通）。

## 6. 失败与修复过程

| 现象 | 原因 | 处理 |
|---|---|---|
| 首次运行点击“重新分析”后无检查 | 计划到达时间 09:00 已过，接口返回 `check_window_closed`（客户端如实提示） | 计划改为 23:00 到达、21:00 检查 |
| `/advisories/{id}` 500 | `unknown` 状态不被读模型接受 | 三端补齐 + 回归测试 + 重导 OpenAPI |
| 契约检查 175→176 期间失败 | 测试断言仍要求三条路由“未覆盖” | 断言改为“已覆盖”，并保留 `/health` 仍未覆盖的检查 |
| 首次页面重开时读到 `None` 检查 | Playwright `browser.new_page()` 新建了隔离上下文，丢了 localStorage/Cookie | 改为同一 context 内 `new_page()` |
| worker 未推进 | 队列为空（提交未成功） | 先确认库内存在 queued 再跑 worker |

## 7. 剩余问题（不要当成已验证）

1. iOS 单元测试与 UI 测试在本机仍受 PTY 限制，需要在普通终端会话补跑；
   本轮只有编译证据。
2. iOS 端没有真实后端联调；聊天、天气、进度逻辑仅有编译 + 代码审阅 + 后端契约保证。
3. 真实 Nexus、真实天气/路线、Bark/APNs 送达未验证；自动分析仍默认关闭。
4. 共享 8010 未启动；原用户数据库仍未找到，未做任何迁移或恢复。
5. `tests/e2e/acceptance_round1_flow.py` 依赖本目录的 `.tmp/backend-venv` 与 3310/8310/8320
   端口空闲，未纳入 CI。

## 8. 建议合入 main 的内容（等待确认后再执行）

- 三个分支的 6 个提交（见第 1 节）与 3 个合并提交。
- 本记录、契约补充（`client-contract.json`、`openapi.json`、第 9.8 节文档）。
- `unknown` 状态兼容修复（后端 + Web + iOS + 回归测试）。
- 新增验收脚本 `tests/e2e/acceptance_round1_flow.py`。

合入后建议在 main 上重跑一次后端测试、网页 `test:e2e` 与 iOS 构建，
再由普通终端补跑 iOS 单元测试，之后才考虑推送与真实环境联调。
