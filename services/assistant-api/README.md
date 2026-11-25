# assistant-api

Personal Assistant 后端负责身份、业务记录、OpenAPI 接口目录、AI 调用参数执行和通用跟进。
NexusPilot 提供模型与 Agent 工作流。Assistant 使用 `matter_plan` 提供业务协议和卡片要求，
通过 `caller_processed` 在同一个 Agent 内处理完整模型正文和实际执行结果。

通用跟进的接口、运行、定向验收和前端任务提示词见
[通用跟进实现与前端联调](../../docs/backend-integration/26-通用跟进实现与前端联调.md)。

本轮接口清单、结构化创建入口、调用参数与 AI 步骤要求见
[接口复核与AI业务调用说明](../../docs/backend-integration/27-接口复核与AI业务调用说明.md)。

浏览器关闭页面后的待办、日程和通勤推送见
[浏览器推送通知](../../docs/backend-integration/29-浏览器推送通知.md)；默认关闭，需独立配置密钥、升级数据库并运行后台 worker。

设计说明见 [`../../docs/backend-integration/`](../../docs/backend-integration/)。
通勤提醒模块的架构、接口示例与 App 联调清单见
[`../../docs/commute/`](../../docs/commute/)。

## 单 Agent 正文处理接入

Assistant 为每次用户任务创建一个 Nexus 运行。创建工作流时使用 `application_response_mode=caller_processed`，并提供模型指令和最终回答的 JSON 结构。Nexus 将完整模型正文发给 `POST /internal/nexus/agent-responses`。Assistant 验证应用凭据和应用标识，按外部账号及 Nexus 运行编号查找本地任务，再检查工作流编号。执行业务接口时使用已核对的账号和本地任务编号 `local_run_id`。

Nexus 将模型正文、实际工具参数和结果保留在原运行中，供下一次模型调用使用。Assistant 在执行业务前保存本次执行编号和请求。同编号同内容可读取已有结果，结果未确认时停止重复写入。最终回答由原运行保存，页面重连后按本地任务编号读取结果。旧 `/internal/nexus/tools` 继续提供既有只读接口。

格式错误返回 `invalid_parameters / failed / continue`，说明决策、参数或卡片的错误位置、字段类型或格式要求，不回传无效字段的原始值。只修正当前输出；先前成功的业务操作保留。

卡片是可选展示：`answer`、`ask_user`和`clarify`都允许省略cards或传`cards=[]`，后端不会自动补卡。用户要求只返回文字、标题或简短确认时，提示Nexus按要求返回文字；追问的question和choices仍保留，不通过后端正则猜用户意图。

`response_cards.presentation_schema()`维护模型输入结构，既发送给Nexus，也由`prepare_cards()`在解析引用前直接校验。`kind=result`的字段value和图片src必须使用`$result`或模板内`$item`引用，规则同样适用于时间线条目；info/plan的普通说明仍可使用常量。图片引用、`$each/template`、数组及秒数显示格式均在输入结构中声明。结构错误返回cards索引和字段路径；引用是否存在、实际结果类型及可展示URL仍需解析后校验。

状态中文显示必须明确选择格式并保持真实引用，例如：

```json
{"label":"状态","value":{"$result":"/todo/status"},"format":"todo_status"}
```

| 显示格式 | 固定转换 |
| --- | --- |
| todo_status | open→未完成，done→已完成 |
| schedule_status | scheduled→已安排，cancelled→已取消 |
| reminder_status | scheduled→待提醒，due→已到提醒时间，acknowledged→已确认，cancelled→已取消 |
| matter_status | active→进行中，waiting→等待中，archived→已归档，deleted→已删除 |

未知状态保留原码，null保留为空，不推断成功；转换后的字段使用客户端既有`format=text`。这是显示转换，不修改接口返回值或数据库状态。卡片顶层status是只读生成字段，兼容旧输入时忽略其传入值，始终由Assistant按真实回执计算；它不等于业务字段value中的状态。

本次定向验证入口：`.venv/bin/python -m pytest tests/test_card_input_contract.py`。17项检查通过，包含真实隔离SQLite待办、错误卡片拒绝、引用修正及中文投影，确认原待办仍为open且未重复创建；没有调用模型或做回归。现有服务未重启，不能据此声称已加载或通过真实模型/浏览器验收。

日程新增、修改及草稿确认发生 `409/conflict_detected` 时，只有正式保存事务已成功回滚，错误才包含 `error.details.formal_write_status=not_applied`，同时提供真实 `conflict`、`draft`（编号、版本、preview 状态）和 `target`（原事项、日程编号）。Assistant 回调返回 `failed / continue` 及完整调用参数、summary 和实际错误，让原 Nexus Agent 决定调整当前提议或 `ask_user`。预览草稿可能已经保存，不等于正式日程已保存；不得未经授权接受冲突或修改其他日程。

没有上述回滚依据的 409、服务异常、超时或提交后响应丢失仍为 `unknown / stop`，相同或新执行编号都不得重做写入。定向验证入口：`.venv/bin/python -m pytest tests/test_agent_repair_feedback.py`，使用临时 SQLite 和替代上游，无真实模型调用；通过不代表运行中服务已加载本次修改。

### 一级接口索引与按需定义

首轮不再按用户文字选择八份完整定义，也不再做否定句过滤或动作优先级推断。`operation_index` 从当前 OpenAPI 生成，每页最多128项简要说明，仅含 `uri`、`summary`、`effect`，并返回 `total/next_offset`。uri已包含接口名称，不重复附上operation_id、HTTP方法和路径；后者在完整定义中提供。用户原文完整保留，操作意图由 Nexus 判断。

看到索引不增加请求，也不要求先查询或解析。按需规则如下：

| 当前情况 | Nexus可采取的动作 |
| --- | --- |
| 普通问答 | 直接answer，不请求定义 |
| 需求不清楚 | 先ask_user，不强制解析 |
| 首次使用本Agent尚未获提供完整定义的业务接口 | 解析其uri，可同时获取多个 |
| 同Agent已获提供该完整定义 | 直接调用，不重复解析 |
| 执行中需要另一项能力 | 只解析新增接口 |
| 只有参数错误、定义未变化 | 按错误反馈修正参数，不重新解析 |
| 回调明确返回operation_definition_stale | 本条未执行，重新取得对应定义后再决定调用 |

“已获提供”只认可当前Run的初始消息，以及该Run成功回调结果中的完整定义和共享结构；不借用服务器缓存标记或上一个Agent的记录。新Agent初始消息会重新提供解析工具定义并清除旧加载标记。业务调用前比较已提供结构与当前发布结构，只有实际变化才报告失效；不计算哈希。解析工具仍按当前请求结构校验，避免共享错误结构更新导致无法取得新定义。

首轮只预加载解析工具 `resolve_operation_definitions` 的完整参数定义。它使用已认证的 `POST /api/v1/operation-catalog/resolve`，请求示例：

```json
{"uris":["assistant://operations/create_todo"]}
```

返回 `api_operations`、共享 `components.schemas` 及 `resolved_uris`。uri只标识本地发布定义，不会访问外部地址或执行业务。Nexus 阅读完整字段、规则及返回结构后，再决定是否提出业务调用；未加载定义的业务调用会被拒绝。每次最多解析8个uri是单次响应大小限制，不是总接口能力限制。

`decision=search` 现在返回简短目录；仅在目录不足时由 Agent 给出 `search_query`，或用空字符串和 `search_offset` 分页读取全目录。它不自动展开参数或后续接口。已获取定义及共享结构在同一个 Agent 继续有效，回调只追加新增或已变化的定义，更新说明替代同名旧说明；完整调用参数、summary和真实结果仍保留。目录不是执行授权，身份、版本、业务规则及重复请求检查保持不变。

定向检查：`.venv/bin/python -m pytest tests/test_operation_index.py`。实际新增模型轮次、token和耗时必须通过同任务真实对比记录，不能把本地目录解析耗时当作模型等待时间；一次样本也不能推断所有任务的长期性能。

Nexus 私有 App 配置需要绑定此路径和独立服务端凭据，不能由模型指定执行地址或身份。
Assistant 新增迁移 `0026_agent_response_receipts`，保存原文、关联身份、实际参数及处理回执。
数据库必须先备份再按授权升级；本次没有迁移真实数据库或重启既有进程。

实现、12 项定向检查、单次真实模型验证及未验收事项统一见
[接口复核与 AI 业务调用说明](../../docs/backend-integration/27-接口复核与AI业务调用说明.md#单-agent-正文处理接入2026-11-05)。
隔离验证通过不代表现有服务已经加载代码；原来的 stub 开发启动方式不支持证明此接入可用。

## 首次空环境准备

以下安装和配置复制只适用于新建环境。已有配置、账号或数据时保留原文件，先核对迁移状态；不得直接用示例覆盖实际配置。

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env          # 仅在 .env 不存在的新环境执行，再填写实际配置
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

历史记录（2026-10-25）在独立工作树上完成了隔离运行验证；运行环境、原库状态、共同联调
恢复方案与客户端交付见
[`../../docs/backend-integration/16-后端运行与客户端交付-20261025.md`](../../docs/backend-integration/16-后端运行与客户端交付-20261025.md)。
`GET /api/v1/commute/runtime` 现在返回 `server_time`，与进程心跳、任务进度分开。

## 已有真实环境的统一管理入口

本机使用 Docker Desktop 和固定 Compose 项目 `superapp-assistant`，由一个 `superapp-assistant-backend` 容器运行统一脚本，管理 API、scheduler 和 worker。原 `.env`、数据库及独立记忆密钥继续位于 Mac，按原绝对路径挂载；不进入镜像，不迁移或重建数据。

```bash
.venv/bin/python scripts/manage_backend.py start
.venv/bin/python scripts/manage_backend.py stop
.venv/bin/python scripts/manage_backend.py restart
.venv/bin/python scripts/manage_backend.py status
.venv/bin/python scripts/manage_backend.py logs
```

默认读取本目录 `.env`，可用 `--env-file /absolute/path/to/existing.env` 显式选择。Docker Desktop 必须运行；宿主只开放 `127.0.0.1:8010`。容器内通过 `host.docker.internal` 连接宿主 Nexus，原配置文件中的 Nexus 地址保持不变。

日常 `restart` 使用 Compose restart，复用原容器；`stop` 保留容器和所有宿主数据。`start` 复用未变化的服务，需要替换时由 Compose 删除旧服务容器，并仅清理本项目孤立容器。依赖变更时先 `build` 再 `start`，不使用 `down --volumes` 或全局 prune。`docker-constraints.txt` 记录切换时本机依赖版本；源代码以只读目录挂载，普通代码修改后统一 restart 即可加载。

内部 `scripts/run_backend.py` 仍可用于原生进程管理，默认仅监听回环地址；不要与 Docker 并行启动。该脚本不负责迁移或开启自动 AI，任一核心子进程退出会停止其余子进程，Docker 的 `unless-stopped` 策略再启动整组。支持 caller_processed 的 Nexus 必须批准应用正文处理连接。

## 上线监测：结构化日志与只读快照

统一入口 `scripts/run_backend.py` 额外管理独立的监测进程。它使用同一配置的本机 SQLite，每 60 秒只读采一次数据；监测进程异常退出会记一条事件并单独重启，不影响 API、scheduler 或 worker 的生命周期。不新增网页、公开接口、数据库表，也不启动模型或地图请求。现有的核心进程退出策略保持不变。

- `http_request`：每次 HTTP 响应记录 UTC 时间、固定路由模板、HTTP 方法、状态码、耗时毫秒数、服务端生成的请求编号和有限错误分类。流式请求的耗时到最后一段响应；中断单独记录。统一入口关闭 Uvicorn 原始访问日志，避免记录完整路径、动态编号或查询串。
- `operations_snapshot`：记录各任务的状态计数、已可执行任务最老等待时间、运行超时或过期租约、近 24 小时失败；区分等待用户、等待延迟重试和实际积压。另记录两个后台心跳、到期未派发及未处理业务变更、SQLite 可读性、迁移版本、大小、所在磁盘余量和主要业务表计数。数据库不可读时队列状态返回 `null`，不显示成空队列。已确认的 SQLite 异常不输出数据库路径或原始错误消息。
- `background_tick_failure` / `monitor_process_exit`：记录后台循环或监测进程故障的类别，不写异常正文。JSON 事件都带 `schema_version`。不输出正文、SQL 参数、密钥、地址查询参数、用户身份或记录正文。请求编号用于定位一次 HTTP 请求，当前未做跨 Nexus 的请求关联或供应商费用采集。

日志继续写入标准输出。设置 `ASSISTANT_OPERATIONS_SPOOL` 后，Assistant 还会把同一批结构化事件按 UTC 日期追加到受保护的 JSON Lines 中转目录。直接查看一条快照可从本目录运行：

```bash
PYTHONPATH=src .venv/bin/python -m assistant_api.operations_monitor --once
```

快照默认每 60 秒产生一次，由 `ASSISTANT_MONITOR_INTERVAL_SECONDS` 控制。队列和派发等待阈值默认 180 秒，由 `ASSISTANT_MONITOR_QUEUE_STALL_SECONDS` 控制。有租约的执行超过实际租约即记为超时，无租约执行按超过 15 分钟未更新判断。数据库快照检查可读性和必要迁移。数据库写入、备份恢复和目标环境依赖需要分别验证。

### 采集、告警与 AI 摘要

采集器每 5 秒读取中转目录，按文件保存读取位置，将允许的字段写入独立的 `operations.sqlite3`。生产配置每 15 分钟聚合一次，用于统计接口状态、路由延迟、队列、数据库和故障类别。每日中转文件和事件明细保留约 14 天，聚合、摘要及告警历史保留 30 天。迟到的事件会更新聚合。如果已经保存 AI 摘要，状态改为 `saved_stale`，供读取者知道原摘要早于最新数据。

| 告警类别 | 开启条件 |
| --- | --- |
| 接口失败 | 一个路由在最近 5 分钟内有至少 3 次 HTTP 5xx |
| 队列积压或超时 | 连续两份可比较的快照报告同一问题。执行结果未知等队列失败立即记录 |
| 数据库异常 | 最新快照报告数据库不可用、必要迁移缺失或磁盘空间不足等 |
| 监测中断 | 超过 3 分钟未采到快照。已有队列和数据库告警保持开启，等待恢复后的快照 |

`alert_state` 保存当前告警，`alert_history` 保存开启及恢复。告警通知暂缓。用只读 SQLite 连接查询告警和摘要：

```sql
SELECT key, opened FROM alert_state ORDER BY opened DESC;
SELECT start, end, ai_state, ai_summary, ai_error FROM windows ORDER BY start DESC LIMIT 10;
```

AI 摘要默认关闭。可选的 `compose.summary.yaml` 需要 Nexus 的用户 API 身份验证已启用，并提供专用于运维的用户编号和具有执行权限的 API 密钥文件。配置 `ASSISTANT_SUMMARY_NEXUS_URL`、`ASSISTANT_SUMMARY_USER_ID`、`ASSISTANT_SUMMARY_KEY_FILE`，可选覆盖 `ASSISTANT_SUMMARY_PROVIDER` 和 `ASSISTANT_SUMMARY_MODEL`。地址须从采集器容器可达。采集器只发送窗口聚合，保存摘要正文及处理状态。授权失败、限流、响应格式错误和传输结果未知分别记录。已尝试的窗口保留其结果，不自动再次请求模型。AI 摘要的开关独立于业务的自动通勤 AI 开关。

### Docker 发布准备

`compose.release.yaml` 从发布镜像运行 Assistant API、scheduler、worker 和监测进程，另起采集器容器。镜像不挂载开发源代码。Web 分支的 `compose.web.release.yaml` 使用独立 Next.js 运行镜像。若共用一个 Compose 项目名，Web 可配置 `ASSISTANT_WEB_API_URL=http://backend:8010`。两个宿主端口默认只开放在回环地址，由现有 HTTPS 反向代理提供入口。

1. 为发布镜像设定明确标签。后端构建使用 `docker compose -p superapp-release -f compose.release.yaml build backend`。在 Web 工作树用相同项目名和 `-f compose.web.release.yaml build web` 构建页面镜像。
2. 将现有运行配置和原记忆密钥放在权限受限的宿主路径，通过 `ASSISTANT_RELEASE_CONFIG`、`ASSISTANT_RELEASE_MEMORY_KEY` 只读挂载。配置文件的记忆密钥路径设为 `/run/assistant-config/memory.key`，数据库地址指向 `/var/lib/assistant/` 下已存在的数据库。设置 `ASSISTANT_RELEASE_DATA`、`ASSISTANT_RELEASE_SPOOL`、`ASSISTANT_RELEASE_OPERATIONS` 为三个独立目录，以及镜像运行者可读取配置和写入这些目录的 `ASSISTANT_UID`、`ASSISTANT_GID`。保留现有注册策略和账号。若启用邀请注册，使用现有受保护配置的 `ASSISTANT_REGISTER_INVITE_CODE`。
3. 切换前在线备份现有数据库，在副本演练迁移与恢复。对正式库执行迁移或切换时，先停下原写入者，避免多个 scheduler/worker 同时消费同一份数据。新版使用镜像内的 `scripts/migrate_sqlite.py upgrade --database <容器内 SQLite 地址>` 对已有库做增量迁移，随后用 `status` 核对。保留原配置、记忆密钥和回退镜像。回退数据库前先核对新版期间产生的数据。
4. 使用 `config --quiet` 检查部署清单，再按后端及采集器、Web 的顺序执行 `up -d --no-build --wait`。验证 Web 到 API、两个后台心跳、数据库采样、采集器写入，以及现有账号和邀请策略。还需核对 Assistant 到真实 Nexus 的连接、Nexus 返回工具调用的 Assistant 入口、模型以及地图和天气。业务自动 AI 建议先保留关闭，在隔离数据验证后再决定是否开启正式环境的 `ASSISTANT_COMMUTE_AUTO_AI_ENABLED`。

## 独立 stub 开发环境（兼容入口）

以下 `dev_env.py` 默认使用 stub 和模拟供应商，适用于独立合成数据调试，不是连接真实 Nexus 的默认启动方式：

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
| POST | `/api/v1/memory/search` | 按关键词匹配 Assistant 自有个人记忆，保留状态、版本、结构化数据和分页 |
| GET | `/api/v1/memory` | 当前账号记忆分页与业务投影；不要求已有 Run，旧 `local_run_id` 可选并核对归属 |
| GET | `/api/v1/memory/settings` | Assistant 账号记忆开关与设置修订号 |
| POST | `/api/v1/memory/commands` | 本人记忆管理；本地支持 typed_data、app_data、undo；旧 disclose 已不适用 |
| GET | `/api/v1/memory/history/{entry_id}` | 本账号条目的本地版本历史；offset/limit 分页 |
| POST | `/api/v1/business-memory/query` | 业务 Agent 按用途读取有效事实、来源及版本；不调用模型 |
| POST | `/api/v1/business-memory/facts` | 当前聊天 Agent 保存用户明确要求记住的逐字原话；直接客户端不能伪造来源 |
| POST | `/api/v1/business-memory/commands` | 当前 Agent 新增、纠正、遗忘；支持回答后候选队列 |
| GET | `/api/v1/memory/operations/{operation_id}` | 本人查询回答后的处理状态 |
| GET | `/api/v1/preferences` | deprecated：旧配置兼容读取；configuration_required=false |
| PATCH | `/api/v1/preferences` | deprecated：旧配置兼容保存；不决定新任务选用记忆，不向模型开放 |
| POST | `/api/v1/preferences/resolve` | 只读解析当前可用默认值、来源和未采用原因；不保存安排或调用模型 |
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
| POST | `/internal/nexus/agent-responses` | 已绑定 App 的完整模型正文处理与执行回执 |

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
高德地点、驾车和公交使用 v3，步行、自行车和电动车使用 v5；均按官方接口使用 GET，Open-Meteo 预报也使用 GET/query；
不宣称供应商链路已经满足“全部URL无参数”。官方说明见
[高德地点搜索](https://developer.amap.com/api/webservice/guide/api/search/)和
[Open-Meteo接口定义](https://github.com/open-meteo/open-meteo/blob/main/openapi/forecast.yml)。
本次未更换供应商或修改其调用协议。

### 路线交通方式与 Agent 预算（2026-11-06）

`POST /api/v1/commute/estimate` 的 `mode`、通勤计划、组合草稿及路线返回统一支持
`driving`、`walking`、`transit`、`bicycling`、`electrobike`。
高德新增三种方式显式请求 `show_fields=cost,polyline`，从 `cost.duration` 读取秒数，
返回真实路线坐标；不以驾车替换失败的步行或骑行，不根据距离虚构耗时。
步行/骑行耗时标为 `static_route_estimate`，不宣称实时路况或未来交通预测。
接口和数据模型兼容原三种方式；不需要新增数据库字段或迁移。现有前端方式选择控件未修改。

业务 Agent 固定输入增加 `route_capabilities`，只公布当前适配器配置支持的方式，
具体账号权限、配额及有无路线仍以每次响应为准。每段交通方式由用户明确指定时必须遵守；
未指定时 AI 可以依据实际测算提出选择。AI 负责地点顺序、交通方式和时间衔接，
具体道路路径由地图供应商计算。HTTP 200 不代表 `data.status=available`。

`ASSISTANT_NEXUS_BUSINESS_MAX_MODEL_CALLS` 默认 24（1–64），
`ASSISTANT_NEXUS_BUSINESS_WALL_TIME_LIMIT_MS` 默认 600000（最多 10 分钟）。
预算在任务创建时保存并传入 Nexus，由同一个 Agent 使用，终答也计一次调用。
Assistant 的工作流请求由 Nexus 按 `personal_assistant` 的 App 配置选定模型。
Nexus 在创建工作流时保存该绑定，重连或查询同一运行继续使用原绑定。
普通非业务调用保留原默认；Assistant 不自行创建下一轮 Agent 或强制改为回答。
运行验证状态见[当前接口与 AI 调用说明](../../docs/backend-integration/27-接口复核与AI业务调用说明.md)。

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

记忆由 Assistant 本地管理，Nexus 只在同一 Agent 中理解相关记忆并请求业务工具。
当前实现、字段、聊天时序、错误及迁移说明统一见
[05：Agent、业务记忆与偏好应用](../../docs/backend-integration/05-Agent-工具-记忆运行流程.md)。

- 本人管理：GET `/api/v1/memory`、`/settings`、`/history/{entry_id}`，POST `/commands`；不依赖 Nexus Run。
- AI 检索：POST `/api/v1/business-memory/query`，开放查询和标签；新任务首轮默认执行一次本地有界预取，后续按需补查。
- AI 修改：POST `/api/v1/business-memory/commands`，operation_id=`mutate_business_memory`；新增、候选、纠正及遗忘，
  使用当前用户原话及 `expected_entry_version`，不接收用户身份、总开关或本人确认权限。
- 独立日程、路线仍使用 `memory_defaults` 保存实际采用的来源；通勤与事项默认共用账号记忆，来源事项不是范围限制。
- 关键读写在终答前完成。非紧急候选可 `execution=after_answer` 登记；返回 queued 不代表保存成功，
  回答持久化后由 Assistant worker 执行本地队列，无需再次调用模型。GET `/api/v1/memory/operations/{operation_id}` 查看结果。

迁移 `0028_owned_memory` 新增本地条目、历史、设置和操作表。正文及来源使用独立密钥加密：
`ASSISTANT_MEMORY_ENCRYPTION_KEY_PATH`。可用 `scripts/configure_memory.py --key-file ...` 创建一次，
不能覆盖已有密钥；SQLite 和密钥须私密备份。应用前备份现有数据并执行 `scripts/migrate_sqlite.py upgrade`。
已有 Nexus 记忆用 `scripts/import_owned_memory.py --user-id ...` 预览，暂停该账号写入后 `--apply` 导入；
保留原编号、全部历史、状态和开关，不删除来源，不双写。目标非空或版本变化时停止。
本机常用开发库已应用 0028，配置独立密钥并保留原 Nexus Assistant 记忆及历史，API 已按原配置重启；服务器不属于本次操作范围。API、scheduler 和 worker 现已由固定 Docker 容器统一管理，使用同一原 `.env` 与数据库；回答后记忆由 worker 处理，不需要再次调用模型。统一启停使用本页上方 `scripts/manage_backend.py`，不再单独启动 Uvicorn 或后台模块。

当前偏好使用由 AI 根据本次输入与检索记忆判断，不要求用户额外开启用途、绑定角色或填设置表。

0030（待办字段和今天页推荐）：新增六个可空待办列和三张独立表，用于保存每周作息、当前推荐与反馈回执。使用现有 SQLite 增量升级入口；在常用库升级前保留备份。接口、输入和范围见[现行产品规划](../../docs/产品规划-工作安排与计划接续.md#6-首版接口与实施状态)。本次仅在隔离库执行升级和定向验证，未更改常用库或重启服务。

0031（追问恢复）：给 `nexus_runs` 增加两个可空字段，保存追问状态与过期时间。升级使用同一个 SQLite 增量脚本，先备份目标库。原执行状态不变。旧追问没有写入这两个字段时，读取按原完成时间计算有效期。

0032（后台随手记与执行进度）：增加三个可空运行列及队列索引。API 受理保存后由现有 worker 执行，并可按原提交编号读取阶段。此版本与 0031 分开记录。使用 SQLite 增量升级入口并先备份目标库，再通过统一入口加载 API 和 worker。

2026-11-10：已补待办、普通/通勤组合草稿、长期通勤计划及明确重新应用的记忆来源记录，说明见 [05 来源记录](../../docs/backend-integration/05-Agent-工具-记忆运行流程.md#本次新增来源记录实现隐私与启用条件)。AI 选择来源，后端只校验/保存；不增加前端入口或默认展示记忆正文。新增 `0029_business_memory_applications` 创建加密的内部来源表，复用原记忆密钥，不改已有账号或业务行。代码曾在隔离库定向验证。2026-11-11 已备份本机 `data/local-development.db` 至 `data/backups/before-0029-20261111-152748/local-development.db`，执行既有 `scripts/migrate_sqlite.py upgrade --database <实际数据库地址>` 完成 0029，再通过 `scripts/run_backend.py --env-file .env` 整组重启 API、scheduler 和 worker。API 在 `127.0.0.1:8010` 返回健康状态 200，两个后台进程均产生新心跳；原账号、业务和记忆记录数量保持不变，原配置及记忆密钥未改动，自动 AI 仍关闭。此次仅核对迁移与运行状态，未调用模型或增加测试；服务器未在本次操作范围。
独立日程和路线接口接收 memory_defaults（按字段提供 entry_id/version/source_quote/value/reason）；
后端核对本任务检索来源、当前版本、引用原文、明确限制和字段类型，保存真实采用来源并返回原 Agent。
已有结构值要求一致；文本解释标 model_interpretation，不改成用户本次输入或已确认长期事实。
参数由 AI 根据本次用户输入选择来源：明确值只传普通字段，采用记忆只传 memory_defaults，不能将记忆值重复抄入普通字段。
同字段或关联字段（如 reminder_at 与提醒记忆）同时出现，包括 null，返回 422/input_source_conflict，写入前停止；原 Agent 可修正后继续。
use_preferences=false 时不提交记忆选择。后端校验归属、版本、原文、字段一致性及重复请求，不替 AI 判断语义来源。未选记忆不自动套旧角色配置。
有歧义由 decision=ask_user 返回 question、choices 和 cards；后端保留卡片及原问题/选项供后续选择解释。
结果卡片的提醒偏移使用 `format=relative_seconds`，耗时使用 `format=duration_seconds`，由 Assistant 将实际秒数转换为带单位的文字；不将原始秒数标成分钟。已有提醒偏移字段的结果引用也按此规则展示，不修改数据库中的秒数或提醒时间。
旧 GET/PATCH preferences 已标 deprecated 并保留原数据兼容，当前只读 resolve 可按需校验，正式保存会再检查。
当前 22 项隔离定向检查通过；未做真实模型语义质量验证或重载服务。
详细参数、来源和询问示例见[偏好应用与对接](../../docs/backend-integration/05-Agent-工具-记忆运行流程.md#对外接口)。

旧 `memory_disclosures` 不再写 Nexus 平台授权；旧通用入口仅校验本地归属和版本。
当前业务 Agent 继续拒绝非空旧授权列表，使用受限记忆工具。本人命令 `disclose` 返回
`memory_disclosure_not_required`，客户端应取消此平台授权前提，不能据此启用原生平台记忆。

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


### 当前8010运行入口

2026-11-11 本机实际通过 `scripts/run_backend.py --env-file <实际 .env 路径>` 统一管理 API、scheduler 和 worker，保持 backend worktree、原配置、数据库及记忆密钥；0028/0029 已迁移，自动 AI、自动天气仍关闭。`scripts/manage_backend.py` 是已有 Docker 管理代码，不代表本机正在使用 Docker。其他机器的 LaunchAgent 名称与目录不作为本机启动依据。

实时定义：`http://127.0.0.1:8010/openapi.json`。前端在Web worktree的3001运行，经`/api/pa`代理访问8010。详细备份、迁移和启动记录见[当前验收文档末尾](../../docs/backend-integration/28-日程与待办独立操作及AI验收.md)。

2026-11-11：待办记忆来源完成一次 Chrome 真实 AI 验收（隔离数据、两个连续输入、6+1 次模型调用、无重试）。首轮按会议时间和记忆保存 09:30 截止待办、cards=[]；用户追问后才展示记忆与计算解释卡片。真实来源记录与页面结果一致，未重复写入；未扩大重测组合草稿、周期计划或重新应用。范围与限制见 [当前流程的验收记录](../../docs/backend-integration/05-Agent-工具-记忆运行流程.md#2026-11-11-待办来源真实浏览器验收)。

本次来源记录的直接依赖已随后续提交补齐，包含待办/日程参数衔接、共享来源结构、校验、本地读取/加密、模型装配和 0028 登记。同一次 Nexus 运行的工具回调和账号、任务归属校验已纳入提交。2026-11-12 纳入首轮记忆检索、本人记忆管理接口和回答保存后由后台执行记忆修改。已有真实 AI 聊天验收使用当时的完整工作区。本次提交未重新运行验收。
