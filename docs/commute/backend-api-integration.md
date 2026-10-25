# 后端持久化与 API 联调记录（2026-10-23）

本轮范围：**后端数据持久化 + 真实 HTTP API 联调**。没有调用真实 WeatherKit、Maps Server API
或 APNs；工作区不存在 Apple 凭证/`.p8`，因此 live 模式仍按设计在缺配置时拒绝启动。
App 不需要在每次调试时连接服务器；Swift 侧保留 `COMMUTE_DEMO=1` 和本地缓存路径，
本轮只把 DTO 对齐到同一份 OpenAPI，并用后端真实响应做离线 fixture 测试。

## 1. 数据替换结论

- 计划、当天实例、检查、天气/ETA 摘要、建议、设备、outbox、推送尝试、动作审计均写入数据库。
- API 读取的是数据库中的最终记录，不是每次请求临时生成的假响应。
- 手动刷新先写 `commute_checks(queued)`，worker 执行后才生成 `commute_advisories`；
  没有 worker 时不会出现“建议已生成”的假状态。
- 自动流程由 scheduler 入队、worker 执行；outbox 与 advisory 在同一事务中落库。
- 当前天气/ETA provider 为显式 `mock`，来源和推送尝试里都写入 `mock`；生产模式拒绝 mock。
- 删除计划在同一事务中删除子数据；本轮 HTTP 验证后 `commute_advisories`/`commute_checks`
  对已删除计划均为 0。

## 2. 可重复执行的 HTTP 联调

```bash
cd services/assistant-api
.venv/bin/python scripts/commute_e2e_check.py
```

脚本会：
1. 在临时 SQLite 库启动真实 uvicorn（随机端口）；
2. 建两个本地账号并登录；
3. 走计划 create/list/get/update、428/409/revision、跨用户隔离；
4. 注册推送设备，手动刷新、幂等重放、worker `--once`、轮询检查、读取建议；
5. 上报 opened/skip_today、验证 Idempotency-Key 冲突；
6. scheduler `--once` 生成自动检查，worker 执行并写 outbox/mock 推送尝试；
7. 删除计划并直接查库确认子数据清理。

2026-10-23 结果：**38/38 checks passed**。
脚本中所有 mock 推送结果都明确来自 `ASSISTANT_COMMUTE_PROVIDER_MODE=mock`，不代表 APNs 送达。

## 3. 生成一份持久化本地联调数据

需要让本地后端数据库里真的有一条计划和建议（不依赖 App、不需要连服务器）时：

```bash
cd services/assistant-api
ASSISTANT_DATABASE_URL='sqlite+aiosqlite:///./commute-dev.db' \
ASSISTANT_ALLOW_SCHEMA_CREATE=true \
ASSISTANT_ENVIRONMENT=development \
ASSISTANT_COMMUTE_PROVIDER_MODE=mock \
ASSISTANT_PROVISION_PASSWORD='<本地开发密码>' \
.venv/bin/python scripts/seed_commute_dev.py --email commute@example.com --reset
```

脚本会写 users/commute_plans/commute_instances/commute_checks/commute_advisories 等真实表，
并打印 plan/advisory id；天气/ETA 来源仍明确为 `mock`。`--reset` 只删除该用户已有计划。
生产环境会拒绝执行。

## 4. 其他验证

| 验证 | 结果 |
|---|---|
| 后端单元/集成测试 `pytest -q` | **92 passed** |
| 后端 HTTP e2e `commute_e2e_check.py` | **38/38 passed** |
| 原有后端 e2e `scripts/e2e_check.py` | 15/15 passed |
| 前端契约 `scripts/check_contract.py` | ok |
| iOS 构建 | 通过 |
| iOS 单测（含真实后端响应 fixture） | **36 passed** |

后端真实响应已保存为 App 离线 fixture：
`ios/LifeAssistant/LifeAssistantTests/Fixtures/advisory_backend_sample.json`。
它来自本轮 `/advisories/latest` 实际响应（mock provider），用于保证 App DTO 不漂移；
不需要启动服务器即可运行。

## 5. 天气查询与建议适用性（2026-10-25 新增）

天气快照的权威关联是 `commute_weather_snapshots.check_id`（唯一）。
`commute_checks.weather_snapshot_id` / `eta_snapshot_id` 从未写入，是废弃占位，
不要读取。

```http
GET /api/v1/commute-plans/{plan_id}/weather?local_service_date=2026-10-23
Authorization: Bearer <local session>
```

```json
{
  "plan_id": "...",
  "plan_revision": 1,
  "local_service_date": "2026-10-23",
  "check_id": "...",
  "provider": "open_meteo",
  "fetched_at": "2026-10-23T00:05:00Z",
  "valid_from": "2026-10-22T23:00:00Z",
  "valid_to": "2026-10-23T06:00:00Z",
  "quality": "fresh",
  "freshness": "fresh",
  "coverage": "full",
  "summary": {
    "forecast_start": "2026-10-23T00:00:00Z",
    "forecast_end": "2026-10-23T01:00:00Z",
    "temperature_c": 18.0,
    "precipitation_probability_max": 0.2,
    "precipitation_probability_known": true,
    "rain_probability_threshold": 0.4,
    "rain_risk": false,
    "coverage": "full",
    "locations": [{"role": "origin", "coverage": "full", "max_probability": 0.2}]
  },
  "stale_after_seconds": 3600,
  "is_stale": false,
  "matches_current_plan": true,
  "current_plan_revision": 1
}
```

- 计划必须属于当前账号（否则 404 `plan_not_found`）；`local_service_date` 缺省为
  计划时区的今天。
- 只返回该计划、该出行日期、`quality != unavailable` 的最新快照；没有就 404
  `NO_WEATHER`，不会借用其他日期或其他地点。
- `is_stale` 由当前时间与 `ASSISTANT_COMMUTE_WEATHER_FRESHNESS_SECONDS` 计算，
  `matches_current_plan` 在计划改版后为 `false`。
- `weather_only` 检查只更新这份天气，不创建、不替换建议。

建议接口新增只读标注，`latest` 仍是“最近保存的建议”，不是“当前有效建议”：

| 字段 | 含义 |
|---|---|
| `matches_current_plan` | 建议的 `plan_revision` 是否等于当前计划版本 |
| `is_current_service_date` | 建议的 `local_service_date` 是否等于计划时区的今天 |
| `is_expired` | 是否已过 `expires_at` |
| `current_plan_revision` | 读取时的当前版本，便于客户端解释差异 |

建议里的 `weather` 与 `sources` 始终是生成时保存的副本；最新天气更新后不会覆盖它们。
客户端应同时展示“最新天气”和“上一次分析采用的天气”，两者不一致是正常的。

### 5.1 读取顺序（2026-10-25 补充）

- 查询某次分析：先 `GET /api/v1/checks/{check_id}` 取 `advisory_id`，再
  `GET /api/v1/advisories/{advisory_id}`；不得用另一条 latest 代替本次结果。
- latest 只表示“最近保存的建议”，配合 `matches_current_plan` / `is_current_service_date` /
  `is_expired` 判断是否仍可展示为当前建议。
- 天气查询是纯读取：不触达 Nexus、不触发模型、不新建建议、不移动 latest。
  只刷新天气的任务（`analysis_mode=weather_only`）完成后，天气会变、建议不变。
- `GET /api/v1/commute/runtime` 的 `server_time` 只回答“服务器是否在响应”；
  `scheduler/worker.state` 是进程心跳，`latest_check.phase_updated_at` 是任务进度。

## 6. 仍未完成

- **真实 WeatherKit REST**：未验证 JWT、数据字段与 Apple attribution。
- **真实 Maps Server API ETA**：未验证 token 交换、地区覆盖和错误码。
- **真实 APNs**：未验证 topic/environment、设备 token、点击与送达。
- 生产 SQLite 上的 scheduler/worker 多进程压测。
- 真实凭证到位后，按 `real-provider-verification.md` 执行验证并更新结果；在此之前不得宣称端到端完成。
