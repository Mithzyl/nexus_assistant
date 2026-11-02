# assistant-api

Personal Assistant 的后端脚手架：本地用户与业务数据 + NexusPilot 应用客户端。
**不含**模型适配器、Agent 循环、工具运行时、记忆存储——这些在 NexusPilot。

设计说明见 [`../../docs/backend-integration/`](../../docs/backend-integration/)。
通勤提醒模块的架构、接口示例与 App 联调清单见
[`../../docs/commute/`](../../docs/commute/)。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env          # 修改两个令牌
.venv/bin/python -m uvicorn assistant_api.main:app --port 8010
```

当前数据库统一为 SQLite。空库初始化与增量升级使用：

```bash
.venv/bin/python -m scripts.migrate_sqlite init
.venv/bin/python -m scripts.migrate_sqlite upgrade
.venv/bin/python -m scripts.migrate_sqlite status
```

`ASSISTANT_ALLOW_SCHEMA_CREATE=true` 只用于本地临时库；正常开发/运行使用迁移器。
接口文档：`http://127.0.0.1:8010/docs`。

本轮（2026-10-25）在独立工作树上完成了隔离运行验证；运行环境、原库状态、共同联调
恢复方案与客户端交付见
[`../../docs/backend-integration/16-后端运行与客户端交付-20261025.md`](../../docs/backend-integration/16-后端运行与客户端交付-20261025.md)。
`GET /api/v1/commute/runtime` 现在返回 `server_time`，与进程心跳、任务进度分开。

## 本机开发环境（可重复启动）

供两个客户端长期连接的本机环境，**不是**共同 8010，也**不是**旧数据恢复：

```bash
.venv/bin/python scripts/dev_env.py init      # 首次：建库/增量升级 + data/dev.env
.venv/bin/python scripts/dev_env.py doctor    # 库结构、端口、进程检查
.venv/bin/python scripts/dev_env.py start     # stub + API(8093) + scheduler + worker
.venv/bin/python scripts/dev_env.py status    # 进程、记录计数、心跳
.venv/bin/python scripts/dev_env.py stop      # 只停本脚本记录的 PID
.venv/bin/python scripts/dev_env.py backup    # SQLite 在线备份 + 校验
.venv/bin/python scripts/dev_env.py restore-check data/backups/<file>.db
```

- 数据：`data/local-development.db`（Git 忽略）；配置 `data/dev.env`；日志
  `data/logs/`；备份 `data/backups/`；PID `data/run/`。
  `start` 不建库、不清库、不重置账号；库缺表/缺列时拒绝启动并提示运行 `init`。
- 默认模拟能力：Nexus 用 `services/nexus-stub`（8191），天气/路线用 mock，
  自动天气检查开启、自动模型分析关闭、不发送通知。
- 需要账号时（密码只走环境变量，线下交付）：

```bash
ASSISTANT_PROVISION_PASSWORD='<一次性密码>' \
  .venv/bin/python scripts/dev_env.py init --seed-email dev-a@example.com
```

详细配置、本轮验证结果与客户端交接见
[`../../docs/backend-integration/16-后端运行与客户端交付-20261025.md`](../../docs/backend-integration/16-后端运行与客户端交付-20261025.md)
第 10 节。

## 通勤提醒

通勤计划、手动检查、建议、设备绑定、通知 outbox 在 API 进程；
定时生成检查与发送通知必须在独立进程：

```bash
.venv/bin/python -m assistant_api.commute.scheduler   # 独立调度器
.venv/bin/python -m assistant_api.commute.worker      # 检查 + 推送 worker
.venv/bin/python -m assistant_api.commute.cleanup --days 30
```

两者均支持 `--once`。默认 `ASSISTANT_COMMUTE_PROVIDER_MODE=mock`，不会访问外部；
生产环境要求 `live`。Open-Meteo 无需密钥；Apple WeatherKit/Maps/APNs 不是启动前置，
缺失时对应能力明确不可用。Bark 由登录账号在 App 内绑定。

本地联调数据（可选，写入真实表；仍使用 mock provider）：
`ASSISTANT_PROVISION_PASSWORD=... .venv/bin/python scripts/seed_commute_dev.py --reset`。

数据库迁移入口：`scripts/migrate_sqlite.py`。
OpenAPI 导出：`.venv/bin/python scripts/export_openapi.py`，输出
[`../../docs/commute/openapi.json`](../../docs/commute/openapi.json)。

## 测试

```bash
.venv/bin/python -m pytest -q                 # 隔离、离线单元/契约测试
.venv/bin/python scripts/e2e_check.py         # 真实 HTTP 校验（自动起两个进程）
.venv/bin/python scripts/commute_e2e_check.py # 通勤持久化/API HTTP 联调（临时库、mock provider）
.venv/bin/python scripts/commute_runtime_e2e.py # 隔离 API+scheduler+worker 运行验收
.venv/bin/python -m scripts.verify_background_migration --database assistant-dev.db
.venv/bin/python -m scripts.backfill_user_asks --database assistant-dev.db  # 副本上迁移并分类
.venv/bin/python scripts/check_contract.py    # 已提交 Web/Swift 客户端字段与 OpenAPI 漂移检查
```

后台运行接口、阶段/状态、weather_only、恢复与 Bark 限制见
[`../../docs/commute/runtime-contract.md`](../../docs/commute/runtime-contract.md)。

前两项不需要外部数据库、MinIO 或外网。`e2e_check.py` 会在 `../nexus-stub` 起一个模拟
NexusPilot 应用 API 的服务，并断言 9 次上游调用里没有任何请求体携带 `user_id`。
通勤测试使用固定时钟与可控 Provider，不调用真实 WeatherKit/Maps/APNs。

## 本地建号

```bash
.venv/bin/python -m assistant_api.commands.seed_dev_user \
  --email me@example.com --display-name 本地用户 --with-sample-data
```

密码从交互提示或 `ASSISTANT_PROVISION_PASSWORD` 读取，不走 argv。生产环境拒绝执行。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/login` | 本地账号登录，签发不透明会话令牌 |
| POST | `/api/v1/auth/logout` | 撤销当前设备会话 |
| GET | `/api/v1/auth/me` | 返回本地身份（不含任何 NexusPilot 标识） |
| POST | `/api/v1/asks` | 交入一句话；写入提问关联并异步启动 general_agent 工作流 |
| GET | `/api/v1/asks` | 当前用户的聊天提问列表（内部任务不在其中） |
| POST | `/api/v1/asks/verify` | 批量核对本地缓存 id：chat / not_chat / unknown |
| GET | `/api/v1/asks/{id}` | 单条请求状态与最终回答（刷新时向上游对账） |
| POST | `/api/v1/asks/{id}/cancel` | 取消（转发 NexusPilot） |
| GET | `/api/v1/asks/{id}/events` | SSE 代理，支持 `Last-Event-ID` |
| POST | `/api/v1/memory/search` | 按关键词匹配 Nexus 新版个人记忆，保留状态、版本、结构化数据和分页 |
| GET | `/api/v1/memory` | 当前账号的 Nexus 个人记忆原始分页；可指定本地 `local_run_id` |
| GET | `/api/v1/memory/settings` | 当前 App 记忆开关与版本 |
| POST | `/api/v1/memory/commands` | 无 Run 用户管理；支持类型化字段、版本纠正、忘记、启停和精确撤销 |
| POST | `/api/v1/data-sources` | 登记设备内容来源与授权范围 |
| GET | `/api/v1/data-sources` | 当前用户的来源、同步状态与授权 |
| PATCH | `/api/v1/data-sources/{id}` | 分别更新同步、助手授权、字段和范围 |
| POST | `/api/v1/data-sources/{id}/sync` | 幂等上传批次；显式删除，不按缺失推断删除 |
| DELETE | `/api/v1/data-sources/{id}/items` | 删除已上传副本，保留来源 |
| DELETE | `/api/v1/data-sources/{id}` | 删除来源及全部副本 |
| GET | `/api/v1/content` | 当前用户内容列表/搜索 |
| GET | `/api/v1/content/{item_id}` | 单条内容 |
| GET | `/api/v1/commute-plans/{plan_id}/weather` | 按计划与出行日期读取最新天气快照 |
| GET | `/api/v1/commute/bark` | 当前账号的 Bark 渠道状态 |
| POST | `/api/v1/commute/bark/binding` | 当前账号保存/替换 Bark Key |
| DELETE | `/api/v1/commute/bark/binding` | 解绑当前账号 |
| PATCH | `/api/v1/commute/bark` | 开启/关闭 Bark 自动提醒 |
| POST | `/api/v1/commute/bark/test` | 显式单次测试发送 |
| GET | `/internal/nexus/tools` | 可注册的业务工具清单（需回调令牌） |
| POST | `/internal/nexus/tools` | NexusPilot 只读工具回调（当前扁平 envelope） |

## 边界

- 用户不需要、也不应该拥有 NexusPilot 账号。当前 NexusPilot 在应用认证时解析/创建
  应用用户；旧的 `nexus_identity_links` 不再是运行前置条件。
- 请求体里没有 `user_id` / `principal_id`；身份只来自会话令牌，外发时写入
  `X-Application-Id` + `X-External-User-Id`，由当前 NexusPilot 创建/解析应用用户。
- 工作流使用当前 `general_agent` / `1.0.0` / `general_tools_v1` 定义；`role_bindings`
  是 assistant-api 的服务端配置，客户端不可见、不可改。
- 工具回调使用当前 `RemoteToolAdapter` 的扁平 envelope：`tool_execution_id`、`application_id`、
  `external_user_id`、`tool_name`、`tool_version`、`arguments`。身份由 NexusPilot 从持久化 Run
  写入，不在模型参数里；`tool_execution_id` 是幂等键。返回值只含 `items` + `next_cursor`。
- 工作流首帧返回后 PA 立即 202；后台消费 SSE 到终态，并只持久化 `final_output.final_text`。
- 创建 Run/工作流启动超时记为 `unknown`，不自动重试：远端可能已经建好了。
- 当前数据库：SQLite + SQLAlchemy/aiosqlite。新增表/列通过
  `scripts/migrate_sqlite.py` 管理。
- 记忆召回失败返回 5xx，不返回空列表——"没有"和"查不到"必须能区分。

记忆管理默认调用 Nexus `/api/v1/app/me/user-memories` 和 `/me/memory-settings`，
不要求先对话，不创建占位 Run。显式传入 `local_run_id` 时仍核验本地归属并使用 Run 路径。
应用凭据和 `X-External-User-Id` 由服务端设置；正文不写入 Assistant SQLite。
搜索每次匹配一个有界上游分页，`next_cursor` 可作为下一次 `offset`。

“我的 → 个人记忆”提供真实分页、范围启停、手动新增／纠正／忘记、四类类型化表单，
以及成功操作后的撤销。相同参数重试保留 key；编辑内容会生成新 key；版本冲突须刷新后
重新选择目标。撤销只使用 `applied_version == version` 的成功结果，不用后来版本替代。
手动类型和敏感确认来自用户勾选，模型工具自动保存不增加确认卡。

- GET/POST `/api/v1/memory/instructions`：仅 Assistant `entry` 指令，禁止 global 和非空
  `allowed_entries`。禁用仍提交有效文字；只影响以后创建的 Run。
- GET/POST `/api/v1/memory/shares`、POST `/shares/revoke`：已有逐条字段许可，创建使用
  记忆版本，撤销使用许可版本。GET `/shared` 仅返回 Nexus 已授权投影及分页。
- POST `/api/v1/memory/shared-disclosures`：显式本地 Run 的共享披露确认。
- 提问和事项接收接口的 `memory_disclosures` 接受共享许可 `grant_id`、许可
  `expected_version`、`confirmed: true`，先确认再启动模型；旧 entry 披露结构仍兼容。
  同一请求重试不能改变授权列表。本应用自己的记忆无需逐次勾选，跨入口共享规则保留。

事项输入版本 5 保留字面用户原文（包括换行和引号），使 Nexus `source_quote` 可以逐字匹配。
正式自动保存仍由 Nexus 模型选择调用工具；不会因业务计划确认而伪造 Run 或静默调用手动保存。
旧通勤候选确认流程保留，不作为自动工具保存的证明。

当前接口限制：App 无历史列表参数，因此关闭页面后不提供完整版本历史；Nexus 原生入口
负责 Assistant→Nexus 自动使用总开关，Assistant 不展示其伪造状态。App diagnostics 只含工具
调用标识、名称、状态及错误，无记忆 entry_id/applied_version；现有公开结果不能可靠关联
自动写入版本，因此暂不展示自动保存成功提示或自动写入撤销。需要 Nexus 提供经过 App
身份校验的写入结果投影；手动管理成功结果的精确撤销已接入。通勤确认后的独立自动写入
仍需 Nexus 提供真实来源与授权接口，不能由 Assistant 构造确认权限替代。

### 网页注册

登录页可切换“创建新账号”，成功后自动进入今天页。仅创建本地账号和会话，
不种入样例事项、日程或记忆；Nexus 身份仍沿用既有的首次访问映射流程。

- `ASSISTANT_REGISTER_ENABLED=true`：默认开放；设为 `false` 时网页隐藏入口且后端拒绝注册。
- `ASSISTANT_REGISTER_INVITE_CODE`：默认空，本机免邀请码；非空时后端恒定时间校验且网页显示必填邀请码。
- 公网部署前明确关闭注册或配置私有邀请码；共享邀请码是注册门槛，不替代网关限流和账户额度管理。不要放入前端公开环境变量。
- `GET /api/v1/auth/register` 仅返回 enabled/invite_required；POST 接收 email、display_name、password（8–256字符）、invite_code，成功返回会话。网页代理只设置 httpOnly Cookie，不把 token 返回页面脚本。

### 正式后端统一启动

在仓库根目录运行 `pnpm start:backend`，或在服务目录运行
`.venv/bin/python scripts/run_backend.py`。统一入口使用服务目录 `.env`，
同时启动 API（127.0.0.1:8010）、通勤 Worker 与 Scheduler；不会启动 Nexus stub，
不会切换数据库或初始化测试数据。Nexus 和 Web 仍为独立服务。
端口已占用时拒绝启动；任一子进程退出会停止整组并返回非零状态，
可由外部进程管理器重启。SIGTERM/SIGINT 会停止本组子进程。
直接运行 uvicorn 仅启动 API，不代表完整后端就绪。

macOS 自动恢复：停止原手动后端组后运行
`.venv/bin/python scripts/install_launch_agent.py --install`。
LaunchAgent `local.superapp.assistant-backend` 登录时启动，退出后节制重启（30 秒）；
退出登录及关机期间不执行，Web/Nexus 仍独立管理。
`ASSISTANT_COMMUTE_JOB_TIMEOUT_SECONDS` 默认 240 秒；超时停止续租，
租约过期后接手，累计达到尝试上限则明确失败。通知未知结果不盲目重发。
# 可选 Nexus 登录与账号关联

原有邮箱密码登录和注册保留。`ASSISTANT_NEXUS_LOGIN_ENABLED` 默认关闭；启用时配置
`ASSISTANT_NEXUS_WEB_URL` 和精确的 `ASSISTANT_NEXUS_LOGIN_CALLBACK_URL`（Web 的
`/nexus-callback`）。回调地址必须同时列入 Nexus 服务端 personal_assistant 的
`login_redirect_uris`；API 使用既有应用凭据，不将凭据发送到浏览器。

已有助手用户先通过原登录进入“我的”，点击“关联 Nexus 账号”，再在 Nexus 登录并确认。
关联不合并历史数据，不自动授权记忆或个性化指令。此后登录页可使用 Nexus 登录：
浏览器跳转到 Nexus `/authorize`，确认后用 fragment 返回一次性授权码；Assistant BFF
核对 HttpOnly 临时 Cookie 中的 state 与 PKCE，将码发送到 Assistant API，再由服务端调用
Nexus `POST /api/v1/auth/app/exchange`。验证映射指向有效本地用户后签发原有 Assistant 会话。
PKCE S256属于标准认证校验，没有引入业务内容哈希。

未关联用户收到提示后仍使用原登录/注册，再显式关联；不按邮箱猜测账号、不自动注册或迁移数据。
关闭功能只关闭新增登录入口，不删除账号或已有会话。解除关联使用 Nexus 的账号关联页；
解除后不再允许通过这条关联新建 Assistant 登录，但现有 Assistant 会话由原退出/到期机制管理。
如需同时终止现有会话，用户仍须退出助手；本次没有设计跨应用强制单点登出。

必要测试：在 services/assistant-api 中运行 `.venv/bin/pytest tests/test_nexus_login.py -q`。
两端真实浏览器验收使用 Nexus 的 `deployments/verification/serve_assistant_login_fixture.py`，
仅创建隔离 MySQL/SQLite 和合成账号，不替换原数据库、不调用模型。
### 2026-10-31 记忆接入验证记录

可用 App 接口已接入并完成下列本次验证；自动写入提示与完整历史仍受上方 Nexus 接口限制，不宣称所有能力均已实现。

- 必要后端回归：在仓库根执行 `services/assistant-api/.venv/bin/python -m pytest -q services/assistant-api/tests/test_memory_management.py services/assistant-api/tests/test_product_memory_bridge.py services/assistant-api/tests/test_run_flow.py services/assistant-api/tests/test_current_app_api_contract.py services/assistant-api/tests/test_intake_stream.py`，27 项通过。
  HTTP 模拟仅证明转发、身份来源、确认顺序与错误处理，不证明真实供应商。
- `pnpm --filter @superapp/web exec tsc --noEmit`、`git diff --check` 通过。
- 本次隔离数据库 `identity_acceptance_d3512c8edd0e`，迁移到 `20261031_0050`，
  使用现有 MySQL/MinIO 容器和独立 Assistant SQLite；仅新建合成账号和测试对象。
  本次脚本位于 `/tmp/assistant-memory-acceptance/`，不是正式运行入口。
- `check.py`：真实 HTTP/存储验证无 Run 列表与保存、稳定 key 重试、账号隔离、纠正、
  旧版本撤销拒绝、纠正与忘记后的撤销、用户指令启停和版本冲突，已通过。
- `model-check.py`：固定模型经过 Assistant→Nexus 工具循环，验证来源引用自动保存、
  关闭后拒绝保存和按需读取，已通过；随后恢复本应用启用状态。
- `native-check.py`：合成 Nexus 用户会话验证总开关默认关闭、开启可召回、关闭后新 Run
  不再通过该许可读取，已通过；最终总开关关闭。
- `sharing-check.py`：真实字段投影仅含获准 label，其他账号共享被拒绝，许可撤销、
  解除关联后跨端管理/共享被拒绝且 Assistant 条目保留，已通过。测试关联最终已解除。
- 双端 Playwright 浏览器：Assistant 新增后刷新仍在；Nexus 选择 Assistant 显示同条记忆，
  纠正后 Assistant 读取版本 2；Assistant 忘记后 Nexus 刷新不再显示，随后 Assistant
  精确撤销恢复版本 4；地点类型表单保存通过。390×844 截图已实际查看，位于
  `output/playwright/assistant-memory-mobile.png`（仓库根路径）。
- 本次未使用付费模型；固定模型结果只证明工具执行和权限规则，不能证明供应商模型会正确
  选择、提取或合并事实。旧 Nexus 报告和以前登录测试未计入本次验收。

- `shared-model-check.py`：真实敏感共享由固定模型验证，未确认时不可读取 label，
  指定 Run 确认后可读取 label，地址始终不进入结果；再次解绑后新 App Run 不再读取，已通过。
- 浏览器用户指令保存后刷新，文本和启用状态保留；长文本及连续双击只发送一次保存请求，
  未产生横向溢出。模拟 HTTP 503 仅用于确认页面错误反馈不显示空列表，随后恢复真实请求。
- `pagination-check.py` 使用合成账号 B 检查 20+1 条分页、去重与结束游标；测试数据保留。
- 最后保留隔离前端 3308（Assistant）、3309（Nexus），API 18308、18309，均只监听本机；
  Nexus API 的所有模型适配器均为固定模型。原有 3000/8010 进程未停止。
  SQLite 和私有运行配置保存在系统临时目录 `nexus-identity-acceptance-j4asfw92` 及
  `/tmp/assistant-memory-acceptance/state.json`；其中凭据不得提交或复制到文档。
  MySQL/MinIO 合成数据未删除，没有启用自动删除。本次没有修改 Nexus 工作区。
- 未验证真实供应商的语义提取／去重、手机端，；App 历史与自动写入回执的后续接入见下节。
  未从自动工具状态推断“已保存”，未实现业务确认后的独立静默写入。

本次修改清单（相对仓库根；这些文件中的既有登录、通勤等差异不归为本次修改）：

- `apps/web/app/(mobile)/me/memories/page.tsx`
- `apps/web/components/mobile/account-memory.tsx`
- `apps/web/components/mobile/memory-management.tsx`（新增）
- `services/assistant-api/src/assistant_api/routers/memory.py`
- `services/assistant-api/src/assistant_api/routers/assistant.py`
- `services/assistant-api/src/assistant_api/schemas/memory.py`（新增）
- `services/assistant-api/src/assistant_api/schemas/matters.py`
- `services/assistant-api/src/assistant_api/nexus_client/client.py`
- `services/assistant-api/src/assistant_api/nexus_client/errors.py`
- `services/assistant-api/src/assistant_api/nexus_client/typed_memory.py`（新增，与 Nexus c4944fe Schema 一致）
- `services/assistant-api/src/assistant_api/services/memory_service.py`
- `services/assistant-api/src/assistant_api/services/run_service.py`
- `services/assistant-api/src/assistant_api/services/matter_plan.py`
- `services/assistant-api/src/assistant_api/main.py`
- `services/assistant-api/tests/test_memory_management.py`（新增）
- `services/assistant-api/tests/test_product_memory_bridge.py`
- `services/assistant-api/README.md`

BFF 继续使用已有 `/api/pa/[...path]` 的 memory 路径和会话 Cookie 转发，不新增凭据下发接口。

事项输入原文和授权结构修改后的直接回归：
`services/assistant-api/.venv/bin/python -m pytest -q services/assistant-api/tests/test_matters.py services/assistant-api/tests/test_matters_v1_rules.py`
结果为 14 项通过。后端两组共 41 项通过；最后错误码类型校验调整后，记忆相关 7 项再次通过。


### 2026-11-01 续接：版本历史与自动写入回执

基于 Nexus 当前工作区已提供的 `history_entry_id` 和 diagnostics `memory_write`，
Assistant 新增无 Run 历史代理、按当前会话及本地 Run 归属查询回执的代理。
个人记忆页可分页查看版本；问助手的新、旧问答记录均可展示真实回执并精确撤销。
回执仅返回条目 ID、写入/当前版本、状态及撤销资格，不下发其他诊断字段。
未取得回执不宣称保存成功；候选不算有效记忆；撤销始终使用 applied_version。

本轮改动：`routers/memory.py`、`tests/test_memory_management.py`、
`apps/web/components/mobile/memory-management.tsx`、新增 `memory-history.tsx` 与
`memory-write-receipts.tsx`、`apps/web/app/(mobile)/ask/page.tsx` 及本说明。

必要验证：
- `services/assistant-api/.venv/bin/python -m pytest -q services/assistant-api/tests/test_memory_management.py services/assistant-api/tests/test_product_memory_bridge.py`：9 项通过。
  模拟 HTTP 仅证明会话身份、Run 归属、回执字段白名单、分页和错误传播；不作为真实存储证据。
- `cd apps/web && npx tsc --noEmit`：通过。
- 恢复原隔离数据与固定模型服务，没有新建账号或新增模型请求。Chrome 3308 页面读取
  5 个真实历史版本；打开既有固定模型对话，显示服务端保存回执，点击撤销后版本由 1
  增至 2、状态为 forgotten，旧回执撤销按钮消失。截图在仓库根
  `output/playwright/memory-history-20261101.png` 和 `memory-undo-20261101.png`。
- `verify-browser-undo.py` 再经真实 Assistant/Nexus API 核对 MySQL/MinIO 持久化结果：
  applied_version 仍为 1、current_version 为 2、can_undo=false；旧版本撤销返回 409；
  合成账号 B 读取账号 A 历史和回执均被拒绝。没有模型调用。
- 本轮未重跑已通过的双端共享、解绑和指令测试；原双端浏览器证据见上节。

依赖与边界：Nexus 对应历史/回执实现仍是其已有未提交改动，本轮只读使用，没有修改 Nexus。
业务确认后的独立静默保存仍需 Nexus 提供可信来源与授权衔接，不能伪造 Run 或确认字段。
真实供应商语义提取/去重未验证。无回执时允许用户重新核对，不把模型自然语言当成功证据。
隔离服务继续保留在 3308/3309、18308/18309，仅本机使用；正式服务未切换。
测试数据和原工作区改动保留；本轮不提交、不推送、不部署。

## Docker 部署

仓库根 `deployments/docker/compose.yml` 包含 Web、API、Worker、Scheduler 和一次性 SQLite 迁移服务。
后端使用同一个持久卷；API 就绪后启动消费者，容器异常退出自动重启。
现有数据库迁移前应先做 SQLite 在线备份，不把本机测试库复制到正式环境。

```bash
# ASSISTANT_ENV_FILE 指向宿主私有后端环境文件（绝对路径），不加入 Git。
export ASSISTANT_ENV_FILE=/absolute/private/assistant.env
# 容器中的 Nexus 地址不能使用指向容器自身的 127.0.0.1。
# Mac 本机 Nexus 可使用 http://host.docker.internal:8000，须核对其监听与可达性。
docker compose -f deployments/docker/compose.yml build
docker compose -f deployments/docker/compose.yml up -d
docker compose -f deployments/docker/compose.yml ps
```

端口默认仅绑定宿主回环地址 3000/8010，可用 `ASSISTANT_WEB_PORT`、`ASSISTANT_API_PORT` 调整。
公网使用需由已有 HTTPS 反向代理转发 Web；生产会话 Cookie 为 Secure。
本机验证可另设 Compose project 名与端口，使用独立数据卷；不覆盖既有服务。
地图 JS Key 为公开构建参数；生产 overlay 使用 `NEXT_PUBLIC_AMAP_SERVICE_HOST=/_AMapService`，
地图安全密钥由私有 Nginx 配置追加，不编译进浏览器。Web 服务 Key 与应用凭据仅供后端使用。

2026-11-01 最小 Docker 验证：两镜像构建成功；独立 `assistant-validation` 项目使用
空数据卷，初始化 39 张表、0 账号。API 健康 200、Web `/login` 200、
Web BFF `/api/auth/nexus` 200，Worker/Scheduler 心跳均存在。
验证端口为 13000/18010；没有执行回归、模型调用或额外哈希校验。
验证配置仅使用临时随机应用令牌，尚未接入正式 Nexus 应用身份，不能作为正式业务部署。
正式目标主机、访问地址及其私有配置需明确后切换。


### 2026-11-01 服务器部署记录

- Assistant 发布目录 `/opt/assistant/releases/20261101-memory`，`/opt/assistant/current` 指向该目录。
  镜像 `assistant-api:20261101-memory`、`assistant-web:20261101-memory` 由当前含未提交改动的源码快照在服务器 AMD64 构建；未提交或推送。
- 正式入口 `https://43.134.11.254:8444`，沿用服务器现有自签公网证书。
  API/Web 回环端口 18011/13001，外部仅 Nginx 8444；主机 UFW 已按用户明确授权放行 TCP 8444。
- Web、API、Worker、Scheduler 与一次性迁移由本仓库 Compose 管理，数据卷 `assistant_assistant-data`；
  空库初始化后 0 账号，两个后台组件心跳正常。未导入本机测试库或真实用户数据。
- 私有配置 `/etc/assistant/api.env`、`compose.env`、`nginx.conf`，均不纳入源码。
  自助注册需要邀请码，存于 `/etc/assistant/registration-invite.txt`（0600）；不在日志或文档展示。
- Nexus 原业务镜像保持 `20261101-15dbeae`；本地后续提交仅部署文档，相关记忆接口已在线。
  仅增加 `personal_assistant` 登记与回调配置，保留其他应用与数据；原配置备份
  `/opt/assistant/backups/20261101-before`。未复制本机 Nexus 或修改其源码。
- Nexus 回调使用 Docker 私网 `https://assistant-gateway`，内部证书在服务器生成，
  保持 TLS 校验；Nexus API/Worker 通过 `/etc/assistant/nexus-overlay.yml` 挂载扩展信任文件。
  持久化 systemd drop-in 位于 `nexuspilot-runtime-clients.service.d/assistant-ca.conf`，
  后续维护须保留该 overlay；公网 `/internal/nexus/tools` 返回 404，私网回调正常。
- 模型绑定生产已启用的 `deepseek/deepseek-flash`，自动付费 AI 调度保持关闭。
  此次无供应商模型请求；只验证构建、迁移、进程、HTTPS、BFF、应用凭据与回调连通性，未跑回归或额外哈希检查。
- 后端应用凭据正常识别：缺少外部用户主体时返回 422，未创建虚构用户映射。
  Nexus→Assistant 私网 HTTPS 健康请求返回 200，Web/API 容器正常。
- 原始部署日志在服务器 `/tmp/assistant-api-build.log`、`assistant-web-build.log`、
  `assistant-web-start.log`、`assistant-nexus-private-connect.log`；不输出或复制秘密。
- 回滚代码时保留数据卷；不可用旧版本覆盖已经产生的新业务数据。
  内部证书有效期 365 天，届时需更新证书及 Nexus 信任挂载后平稳重启相关服务。
- 最终公网检查：MacBook Air 直接连接（禁用环境代理、使用服务器公开证书验证）
  `/login` 返回 200，`/api/auth/nexus` 返回 200 且 enabled=true，
  `/internal/nexus/tools` 返回 404。原 Nexus 8443 同期返回 200。
  最初 8444 外部 TLS 失败，腾讯云控制台确认缺少端口规则；用户授权后通过真实 Chrome
  在指定实例新增唯一 TCP 8444/全部 IPv4/允许规则（Assistant HTTPS），随后上述请求通过。
  没有修改其他云防火墙规则，截图 `output/playwright/assistant-cloud-port-8444.png`。
- 浏览器沿用现有自签证书，未替用户绕过浏览器安全警告；如出现警告需用户自行处理信任。
  生产登录、注册与业务语义未重跑，首个正式账号留待用户建立。

### 2026-11-01 通勤表单地点搜索修复

通勤新建/编辑表单的起终点各使用一个地点搜索框，停止输入 600ms 后从服务端查询高德候选。
选择候选后整体写入名称、城市、坐标和坐标系；改写名称立即清除旧坐标，避免名称与位置不一致。
城市和手填坐标收进高级选项；同名地点由用户选择，不自动选第一项。
后端新增登录态 `GET /api/v1/commute/places?query=...`，复用现有地点 Provider，
不创建事项草稿或模型 Run；原事项草稿地点接口保持原权限校验。
必要检查仅包含前端类型检查、一个隔离搜索权限/无副作用测试及实际网页搜索与选择；
不运行回归、不做额外哈希、不保存测试通勤计划。
发布镜像标签 `20261101-places`，保留上一版本与数据卷；本次不更改 Nexus。
- 本次已切换至 `/opt/assistant/releases/20261101-places`，API/Web/Worker/Scheduler
  使用 `20261101-places` 镜像；切换前 SQLite 在线备份位于数据卷
  `/data/backups/20261101-before-places.db`，原数据保留。
- 必要网页验证：正式 Chrome 新建页输入公开地点“柳州市博物馆”，自动返回 5 个真实高德候选，
  点击“柳州博物馆 · 柳州市 · 解放北路37号”后显示“已确认地点 · 柳州市”；未提交保存计划。
  截图 `output/playwright/commute-place-candidates.png`。未调用模型，没有运行回归或哈希检查。
- 必要上线检查发现共享 Docker 网络的 `web` 名称与 Nexus 重名，已在生产 overlay
  为 Assistant Web 配置唯一 `assistant-web` 别名，私有 Nginx 上游改为同名地址。
  后续发布应保持此别名，避免误路由到 Nexus Web；没有改动 Nexus 业务代码。

### 2026-11-01 流式错误与 Worker 终态修复

正式发布 `/opt/assistant/releases/20261101-stream`，镜像标签 `20261101-stream`。
此次仅修改 Assistant，不修改 Nexus 源码。SQLite 在线备份保留于
`/data/backups/20261101-before-stream.db`，原发布配置与旧镜像保留。

- SSE `error` 帧返回 `error`（Nexus WorkflowErrorRead 六字段）、`status`、`outcome`、
  `local_run_id`、`workflow_execution_id`。保留已知失败/结果未知/已取消的区别，不回传原始异常。
- 六字段为 `error_code`、`error_type`、`public_message`、`is_retryable`、
  `outcome_is_known`、`details_reference_id`；仅传上游实际提供的公开错误，不从文字猜账务状态。
- Worker 启动的有限事件回放结束后，通过同一工作流 events 与 after_sequence 续读。
  只首次 POST 创建工作流；后续全为 GET，整体受流式超时约束。
- 历史读取仅对尚未确定终态的当前用户记录查询已有 Nexus 工作流，不重新运行模型。
  网页显示真实失败及错误码，未知请求不再显示“修改是否保存”的无关提示。
- `tests/test_intake_stream_errors.py` 三项必要确定性检查通过，前端类型检查与生产构建通过。
  HTTP 模拟只证明错误字段、有限事件续读无二次 POST、历史核对与账号隔离，不代表供应商联调。
- 正式 Chrome 读取此前两次“你好”，实际显示处理失败及 invalid_request；没有发送新消息。
  截图 `output/playwright/assistant-stream-failure-state.png`。
- OpenCode Go 切换已获用户授权并完成，实际配置与限制见下节。
- 本次没有付费模型调用、回归或额外内容哈希检查，没有提交或推送。


### 2026-11-01 OpenCode Go 生产绑定

用户明确批准复制本机已有 OpenCode Go API Key 至指定服务器私有配置，并登记 Go 端点。
实际启用：`opencode / deepseek-v4.1-flash`，Base URL 为
`https://opencode.ai/inference/go/openai/v1`。Assistant 的 controller 绑定已切换；
其他供应商配置保留。Nexus API/Worker 和 Assistant API/Worker/Scheduler 已重载配置。
没有重建镜像、修改 Nexus 源码或运行本地开发专用注册命令。

端点经过现有 URL 校验，模型及能力快照经 Nexus 当前模型目录服务登记，复用本机已有的
来源元数据；能力快照保持 `live_generation_verified=false`，不是本次真实供应商验证。
私有配置仅记录在服务器 `/etc/nexuspilot/config/api.env`、`/etc/assistant/api.env`；
切换前备份为 `/opt/assistant/backups/20261101-opencode`（0700），配置文件0600。
日志不输出 Key 或完整私密绑定字段，核验只输出是否匹配目标的布尔值。

实际核对：Nexus 已加载 Key、允许指定模型，数据库中端点启用、模型 enabled；
Assistant 运行时绑定与目标匹配，API 健康、后台组件运行、Nexus systemd 单元 active。
本次无供应商模型调用，没有付费验证、回归或额外哈希检查；凭据是否被供应商接受及
真实生成质量未在本次验证。切换作用于之后创建的 Run，历史失败记录与固定绑定不改写，
不会自动重跑旧失败请求。未提交或推送代码。
