# assistant-api

Personal Assistant 后端负责身份、业务记录、OpenAPI 接口目录、AI 调用参数执行和通用跟进。
NexusPilot 提供模型与 Agent 工作流，Assistant 使用现有 matter_plan 组织接口调用和卡片要求。

通用跟进的接口、运行、定向验收和前端任务提示词见
[通用跟进实现与前端联调](../../docs/backend-integration/26-通用跟进实现与前端联调.md)。

本轮接口清单、结构化创建入口、调用参数与 AI 步骤要求见
[接口复核与AI业务调用说明](../../docs/backend-integration/27-接口复核与AI业务调用说明.md)。

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
.venv/bin/python -m assistant_api.background scheduler   # 通用调度器
.venv/bin/python -m assistant_api.background worker      # 通用跟进 + 供应商队列
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
| POST | `/api/v1/memory/commands` | 显式用户记忆操作；必填本地 `local_run_id`，支持现有 Nexus `app_data` |
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

## 事项补充与 AI 来源记录

用户保存说明、备注或进展时调用 `POST /api/v1/matters/{matter_id}/supplements`，
请求体为 `text`、`idempotency_key`，可选 `parent_text_id`、`relation_type`。后端在同一事务保存原文、
`kind=supplement` 的事项进展及后台任务，不等待模型返回，也不修改日程或待办。
统一后台 worker 使用现有 Nexus 调用服务整理内容，生成文本单独标记为 `origin=assistant`；
用户原文为 `origin=user`。采纳保留 AI 来源，用户修订另存并关联原文本。

纠正传 `relation_type=correction`，回答传 `relation_type=answer`，二者都必须提供
`parent_text_id`。回答仅允许关联当前账号、同一事项内的 AI 问题；纠正可以关联该事项内
的用户或 AI 文字。为兼容既有客户端，关联用户文字的 `purpose` 仍为 `user_revision`，
具体动作由 `relation_type` 区分；旧客户端未提供类型的记录保持为空，不猜测历史动作。

补充详情和列表在每段 `texts[]` 上返回 `corrections`、`answers`、`untyped_relations`，
每类最多五条预览，同时提供各类总数、`has_corrections`、`has_answers` 和
`relations_truncated`。这些字段只统计直接关联的用户文字，保留原文和采纳历史。
`has_answers` 只代表收到回答，不代表问题已解决。完整记录可通过
`GET /api/v1/matters/{matter_id}/supplements/{supplement_id}/texts/{text_id}/relations`
分页读取，`relation_type` 可为 `correction`、`answer`、`related` 或省略；
`before_id` 使用关联文字的 `text_id`，不是补充编号。查询不会调用 AI。

需要运行增量迁移 `0024_matter_supplements`：现有 `scripts/migrate_sqlite.py upgrade`
会创建 `matter_supplements` 和 `supplement_texts`，不修改既有记录的作者。
后续 `0025_supplement_relations` 仅添加可空的 `supplement_texts.relation_type`
和关系查询索引，保留历史正文、作者、采纳信息及原请求内容。不要用猜测回填历史类型。
部署前按既有要求备份数据库并显式指定正确数据库；本次代码验证不迁移本机业务数据库。
API 和统一 worker 需要加载相同版本；不需要新增定时器，也不启用通勤自动 AI 开关。
后台整理源于用户明确保存，按每条补充调用一次；失败不自动重复调用。
未知结果不允许重试，避免重复付费；工作中断由持久化状态记录为未知，原文仍保留。

读取单条或列表使用 `GET /api/v1/matters/{matter_id}/supplements[/{supplement_id}]`，
查询不会发起 AI；采纳使用 `POST .../{supplement_id}/texts/{text_id}/accept`，请求体 `{}`；
用户明确重试失败整理使用 `POST .../{supplement_id}/retry`，请求体 `{}`，最多三次。
只有失败可重试；排队或处理中的重复请求返回当前状态，成功和未知结果不可重试。
所有接口均核对当前账号及事项归属，来源与采纳账号由后端填写，不接受客户端或模型指定。

日程及待办变更仍使用 `POST /api/v1/matters/{matter_id}/intake`，调用既有接口规划与执行流程。
新规划上下文保存实际账号、用户原始输入、带作者的历史消息及当前事项最近八条补充，
并有界补入所关联的原文字、问题、用户纠正和回答（最多 64 段，父级最多八层）。
补充总文本最多 14000 字符、单段最多 4000 字符，关联用户文字及原文优先，截断有明确标记。
保留每段的关系类型、原文编号、关联数量和资料缺失标记，已采纳摘要后续被纠正时仍须读取纠正。
未采纳建议不作为用户事实或操作授权；采纳也不单独授权修改安排。
后台跟进触发标记为系统来源，不冒充用户刚发出的消息。旧进展作者不猜测回填。

关系补齐的定向验证只运行 `tests/test_supplement_relations.py`：使用临时 SQLite、
合成账号和受控 Nexus 返回，验证旧数据迁移、纠正/回答区分、关联分页、权限和 AI 输入。
它不调用真实模型，不修改本机业务数据库，不运行其他回归检查。

一次定向验证入口为 `scripts/verify_matter_supplements.py --env-file <现有配置绝对路径>
--output-dir <新建的隔离目录>`；需设置 `PYTHONPATH=src` 并使用现有虚拟环境。
该命令会在新目录中创建独立数据库、使用合成账号，通过真实 Nexus/Worker 验证一次补充整理
及一次包含原日程和待办修改的会话，会产生真实模型费用，不能当作普通本地检查随意运行。
只在明确授权后执行；不运行前端、地图、天气、通知或回归检查，不改原有环境和数据。

完整字段、保存与查询步骤、来源处理及前端实施提示词见
[事项补充与安排变更前端对接提示词](../../docs/backend-integration/29-事项补充与安排变更前端对接提示词.txt)。

## 共享地点查询（通勤与日程）

地点查询通过现有 `PlaceProvider` / `AmapProvider` 提供；
`services/place_queries.py` 只负责查询，不读取或创建事项、日程、草稿。
`services/commute_places.py` 在相同查询结果上添加原有账号及草稿绑定的候选令牌。

需登录，以下两个新增接口均只读，发布给 AI 的 `x-assistant.effect` 为 `read`：

| 接口 | JSON 请求体 | 返回 |
| --- | --- | --- |
| `POST /api/v1/places/search` | `query` 必填（1–255字符）；可选 `city`（最多64字符）、`search_type`（`place`或`address`，默认`place`）、`limit`（1–10，默认5） | `query/city/search_type/ambiguous/candidates/reason_code`；候选包含真实名称、地址、城市、经纬度、坐标系及来源，不包含草稿 `selection_token` |
| `POST /api/v1/places/map-pick` | `latitude`（−90至90）、`longitude`（−180至180）必填；`coordinate_system` 只能是 `gcj02`（默认值） | `status/reason_code/address_notice/location`；当前位置附近地址，不保证是精确 POI，不包含草稿令牌 |

这两个接口不需要 `draft_id`。查询条件全部放在 JSON，请求 URL 不得含查询参数；
携带查询参数返回 `422 unexpected_query_parameters`。结果为空和供应商不可用通过
`reason_code` 区分，不能把供应商失败解释为“没有地点”。

原 `POST /api/v1/commute/places/search`、`map-pick`、`confirm` 路径保留：
前两者复用共享查询，仍校验当前用户的真实草稿，并签发原候选令牌。
`confirm` 的实际参数为 `draft_id` 和 `selection_token`，不接收 `expected_version`；
只更新草稿。不能把通用查询结果当作草稿令牌提交，也不能为搜索编造草稿 ID。
这些地点接口同样拒绝 URL 查询参数。

供应商限制：以上保证浏览器→Web代理→Assistant 的地点查询使用固定URL及JSON体。
现有高德适配器仍按其 v3 官方接口使用 GET，Open-Meteo 预报也使用 GET/query；
不宣称供应商链路已经满足“全部URL无参数”。官方说明见
[高德地点搜索](https://developer.amap.com/api/webservice/guide/api/search/)和
[Open-Meteo接口定义](https://github.com/open-meteo/open-meteo/blob/main/openapi/forecast.yml)。
本次未更换供应商或修改其调用协议。

本次验证使用内存 SQLite 和 mock 地点供应商，仅执行新增
`tests/test_place_lookup.py` 中的定向检查，不作为真实供应商、浏览器地图或生产业务验收。

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

记忆桥接复用 `/api/v1/app/runs/{run_id}/user-memories` 和 `memory-settings`。
服务端从当前账号的本地 Run 解析 Nexus Run；调用者不能指定远端身份或跨账号 Run。
没有已建立的 Run 时返回 409 `memory_context_required`，不创建占位 Run，也不宣称记忆为空。
搜索为正文关键词匹配，每次读取一个有界上游分页；`next_cursor` 可作为下一次 `offset`。
`commands` 保留 Nexus 的候选确认、敏感内容确认、版本与幂等语义；`app_data.namespace`
固定为 `personal_assistant`。此桥接不会自动提取历史，也不会把日程视为记忆。

`POST /api/v1/asks` 可显式传入 `memory_disclosures`（最多 10 条，每条包含
`entry_id`、当前 `expected_version`、`confirmed_sensitive: true`）。服务端先在新建的
当前 Nexus Run 上执行既有 `disclose`，再启动模型；失败时不启动模型。默认不披露，
授权只适用于该次对话，幂等重放不能改变授权列表。Nexus 继续核验归属和版本。
网页当前只有记忆检索展示，尚未提供选择记忆并授权本次 AI 对话的交互。

### 网页注册

登录页可切换“创建新账号”，成功后自动进入今天页。仅创建本地账号和会话，
不种入样例事项、日程或记忆；Nexus 身份仍沿用既有的首次访问映射流程。

- `ASSISTANT_REGISTER_ENABLED=true`：默认开放；设为 `false` 时网页隐藏入口且后端拒绝注册。
- `ASSISTANT_REGISTER_INVITE_CODE`：默认空，本机免邀请码；非空时后端恒定时间校验且网页显示必填邀请码。
- 公网部署前明确关闭注册或配置私有邀请码；共享邀请码是注册门槛，不替代网关限流和账户额度管理。不要放入前端公开环境变量。
- `GET /api/v1/auth/register` 仅返回 enabled/invite_required；POST 接收 email、display_name、password（8–256字符）、invite_code，成功返回会话。网页代理只设置 httpOnly Cookie，不把 token 返回页面脚本。

[日程与待办独立操作及AI验收](../../docs/backend-integration/28-日程与待办独立操作及AI验收.md)：独立接口、可选组合、明确提醒依赖、迁移及16个真实AI场景。


### 单日通勤修改、即时重测与前晚更新

`POST /api/v1/commute-plans/{plan_id}/days/query`、`PATCH /api/v1/commute-plans/{plan_id}/days`和`POST .../days/refresh`（service_date统一放JSON payload）提供完整的读取、修改重测及即时刷新操作；长期PUT对完整关联计划立即刷新下一次有效通勤，并返回next_day。详见[当前接口与定向验收](../../docs/backend-integration/28-日程与待办独立操作及AI验收.md)。新版本需要迁移0023_commute_day_override，新增可空当天到达覆盖值；不改写旧业务行。

自动更新继续使用现有统一background scheduler/worker及commute_auto_ai_enabled配置，前晚当地22:00和当天检查时间各建立一次持久跟进。本次10条缺陷定向用例使用`pytest -q tests/test_three_operation_repairs.py`；真实Nexus脚本`evaluate_intent_commute.py`必须显式选择--cases，后台22点用例必须独占新临时库。不要用默认业务库执行这些验收。


### 当前共同8010运行入口（2026-11-03）

当前LaunchAgent `local.superapp.assistant-backend`已运行本backend worktree的`scripts/run_backend.py --env-file /Users/mith/Desktop/project/superapp-next/services/assistant-api/.env`，复用原库及凭据；子进程为API与统一background scheduler/worker。原库已完成0021/0022/0023增量升级，原业务行保持。自动AI、自动天气配置仍为false。不要再从主目录旧run_backend.py并行启动另一套服务。

实时定义：`http://127.0.0.1:8010/openapi.json`。前端在Web worktree的3001运行，经`/api/pa`代理访问8010。详细备份、迁移和启动记录见[当前验收文档末尾](../../docs/backend-integration/28-日程与待办独立操作及AI验收.md)。
