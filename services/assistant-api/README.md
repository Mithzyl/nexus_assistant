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
