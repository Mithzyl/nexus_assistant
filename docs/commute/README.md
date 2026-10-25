# 服务器定时通勤提醒（commute reminders）

本模块在现有 `assistant-api`（FastAPI + SQLAlchemy）内实现，没有另起服务、没有迁移技术栈。
API 进程只负责鉴权、校验、持久化和读取；**定时任务在独立的 scheduler / worker 进程**，
使用数据库租约与条件更新防重，不依赖 Web 进程内定时器，也不要求 Redis（现有技术栈没有，
数据库已能提供租约和唯一约束；若以后引入 Redis，只能作为加速层，不能取代数据库幂等键）。

## 1. 进程与职责

| 进程 | 命令 | 职责 |
|---|---|---|
| API | `uvicorn assistant_api.main:app` | 计划 CRUD、手动检查入队、建议/检查读取、设备绑定、动作上报 |
| Scheduler | `python -m assistant_api.commute.scheduler` | 按计划 IANA 时区建立当天实例，每 10 分钟入队一次自动检查，关闭过期实例 |
| Worker | `python -m assistant_api.commute.worker` | 领取检查、生成建议、写通知 outbox、发送 Bark |
| Cleanup | `python -m assistant_api.commute.cleanup --days 30` | 清理过期执行历史（不删待发 outbox） |

两个后台入口都支持 `--once`，便于本地验证和演练。生产环境必须让它们作为常驻进程运行，
并由部署系统做重启；API worker 数量与调度无关。

## 2. 数据与幂等

当前数据库为 SQLite。初始化/升级入口：[`scripts/migrate_sqlite.py`](../../services/assistant-api/scripts/migrate_sqlite.py)。

| 表 | 作用 | 关键唯一约束 |
|---|---|---|
| `commute_plans` | 固定通勤地点与日程 | `(user_id)` + `revision` |
| `commute_instances` | 每计划每本地日执行实例 | `(plan_id, local_service_date)` |
| `commute_checks` | 手动/自动检查任务 | `(instance_id, plan_revision, slot_index)` |
| `commute_weather_snapshots` / `commute_eta_snapshots` | 供应商摘要，不含坐标 | `check_id` |
| `commute_advisories` | 建议事实与文案 | `check_id` |
| `push_devices` | 安装实例绑定 | `(user_id, installation_id)` |
| `notification_outbox` | 逻辑通知 outbox | `(plan_id, local_service_date, reminder_sequence)` |
| `push_attempts` | 每设备发送结果（只存 token 摘要） | — |
| `commute_advisory_actions` | `opened` / `skip_today` 审计 | `(advisory_id, action, installation_id)` |
| `commute_idempotency_keys` | 用户 + 路由 scope 的重放 | `(user_id, scope, key)` |
| `commute_leases` | scheduler 单例租约 | `lease_name` |

`commute_checks` / `notification_outbox` 都是“先条件 UPDATE 抢占，再执行”，进程崩溃后
租约过期可被重新领取。当前部署只发送 Bark；`accepted_by_bark` 只表示 Bark 接收请求，
不代表手机收到或用户阅读。APNs 代码保留但不可达。

## 3. 调度规则（产品默认值，可配置）

1. 本地日与目标到达时间只由计划的 IANA 时区和 `arrival_local_time` 生成，不用服务器时区。
2. 从 `check_local_time` 起，每 10 分钟最多一次自动检查；到建议出发时间、跳过当天、计划关闭、
   目标到达时间中任一先发生即停。
3. 首次有效检查创建建议并尝试一次通知。后续只有建议出发时间**提前≥10 分钟**或降雨风险跨越阈值，
   才允许新增建议/通知。每计划每日最多 2 次自动提醒（跨 revision），两次间隔≥15 分钟。
   自动不可用提示也计入这 2 次，每天最多 1 次简短不可用提示。
4. 首次检查已过建议出发但未到目标到达：允许一条“现在出发可能偏晚”，真实反映时间。
   已过目标到达：不补发、不创建历史实例。
5. 夏令时缺失时刻顺延到首个有效时刻；重复时刻取第一次，且按本地分钟槽的数据库唯一键
   再执行一次去重。第一版拒绝跨午夜（`check_local_time >= arrival_local_time` 返回 422）。

## 4. 事实与新鲜度

- **ETA**：请求携带未来 `departAt`，优先使用上一条建议的未来出发时间，否则使用
  `target_arrival - buffer`；都不可用时才用 `now_after_planned_basis`，并把基准写入
  `commute_eta_snapshots.request_depart_at` / `response_basis`。没有“现在出发”耗时的无条件复用。
- **天气**：取 `[预计出发 - 最大雨天预留, 目标到达]`，同时请求出发地和目的地；按重叠小时段
  计算最大降雨概率。该判断是两地相关预报时段的近似，**不是整条道路的逐点天气**。
  缺失概率保持 `null`，不按 0 处理。
- **新鲜度**：ETA 10 分钟、天气获取时间 60 分钟，同时检查预报是否覆盖出行时段。
  超时/缺失标记 `degraded` / `unavailable`，不会把旧数据称为实时。
- ETA 缺失：不生成精确出发时间；天气缺失但 ETA 有效：提供路线建议并明确“天气暂不可用”；
  两者缺失：`quality=unavailable`、检查 `failed`，仍保留一条如实的不可用建议。
- 文案先由确定性代码生成标题/摘要；可选模型只改文案。模型输出中出现模板之外的数字、
  单位或“预警/警报”时立即丢弃并使用模板；超时不影响通知。

## 5. API

完整机器可读契约：[`openapi.json`](openapi.json)。请求示例见 [api-examples.md](api-examples.md)，
App 交互与推送处理见 [app-integration-checklist.md](app-integration-checklist.md)。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/commute-plans` | 当前用户的计划 |
| POST | `/api/v1/commute-plans` | 创建（201 + ETag） |
| GET | `/api/v1/commute-plans/{id}` | 详情（ETag） |
| PUT | `/api/v1/commute-plans/{id}` | 全量更新，必须 `If-Match: "revision"`，冲突 409 |
| DELETE | `/api/v1/commute-plans/{id}` | 删除并清理子数据、停止调度 |
| POST | `/api/v1/commute-plans/{id}/checks` | 手动刷新，必须 `Idempotency-Key`，202 |
| GET | `/api/v1/checks/{id}` | queued/running/succeeded/degraded/failed/cancelled |
| GET | `/api/v1/commute-plans/{id}/advisories/latest` | 最新建议，无则 404 `NO_ADVISORY` |
| GET | `/api/v1/advisories/{id}` | 建议详情 |
| PUT | `/api/v1/push-devices/{installation_id}` | 遗留 APNs 设备登记；当前不用于发送 |
| GET | `/api/v1/commute/bark` | Bark 渠道状态 |
| PATCH | `/api/v1/commute/bark` | 开启/关闭 Bark 自动提醒 |
| POST | `/api/v1/commute/bark/test` | 显式测试发送 |
| DELETE | `/api/v1/push-devices/{installation_id}` | 注销当前设备 |
| POST | `/api/v1/advisories/{id}/actions` | `opened` / `skip_today`，必须 `Idempotency-Key` |

所有错误为 `{error:{code,message,retryable},request_id}`；时间戳为带 `Z` 的 UTC；
星期为 ISO 1–7；距离米、耗时秒、温度摄氏度、概率 0–1。`user_id` 不来自请求体。

## 6. 本地启动（Mock，不访问 Apple）

```bash
cd services/assistant-api
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# 第一次本地建表；正常运行使用 scripts/migrate_sqlite.py
ASSISTANT_ALLOW_SCHEMA_CREATE=true .venv/bin/python -m uvicorn assistant_api.main:app --port 8010

# 另一个终端：独立调度器 + worker（固定 20s/3s 轮询）
.venv/bin/python -m assistant_api.commute.scheduler
.venv/bin/python -m assistant_api.commute.worker
```

Mock 建议的 `sources.provider` 会是 `mock`，推送结果显示的 provider 也是 mock；
它只用于开发和测试，**生产启动会拒绝 mock 配置**。单元测试中的 push `accepted` 不是
真实 Bark 接受（`accepted_by_bark`）；Bark 接受不等于手机收到。

建示例账号见主 README。手动触发一次调度与 worker：

```bash
.venv/bin/python -m assistant_api.commute.scheduler --once
.venv/bin/python -m assistant_api.commute.worker --once   # 可重复执行，处理下一个检查/outbox
```

## 7. 后端数据与 API 联调

本轮已完成后端持久化与真实 HTTP 联调（App 不参与）：

```bash
cd services/assistant-api
.venv/bin/python scripts/commute_e2e_check.py   # 38 项 HTTP 检查，临时库，不调用 Apple
```

记录见 [backend-api-integration.md](backend-api-integration.md)。需要本地库直接有一条计划/建议时，
可用 `scripts/seed_commute_dev.py` 写入真实表（仍为 mock provider，不调用 Apple）。
App 的日常调试不要求每次连服务器：
可用 `COMMUTE_DEMO=1` 走本地 mock，或读取上次缓存；远端模式只在配置了后端令牌时使用。
Swift DTO 已按同一份 OpenAPI 对齐，并用后端真实响应 fixture（mock provider）做离线解码测试。

## 8. 生产配置

复制 `.env.example` 并设置：
- `ASSISTANT_ENVIRONMENT=production`
- `ASSISTANT_COMMUTE_PROVIDER_MODE=live`
- Bark：登录用户在 App 内绑定 Key，后端按账号明文保存（开发期）
- APNs/WeatherKit/Maps：不是当前启动前置条件；Apple 代码保留，当前不可达/未配置
- 天气使用 Open-Meteo，不要求 WeatherKit/Maps 服务端密钥
- `ASSISTANT_COMMUTE_APNS_TOPIC` 与 `..._ENVIRONMENT` 只能服务端配置，客户端不可选择
- 可选模型 URL 指向受控后端接口；不配置则模板文案

生产启动时任一缺失会抛 `CommuteConfigurationError` 并拒绝启动，不会静默使用假天气/假推送。
部署单元、监控与回滚见 [operations.md](operations.md)。

## 9. 测试与已验证范围

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/commute_runtime_e2e.py   # 隔离 API/scheduler/worker 真实 HTTP
.venv/bin/python -m scripts.verify_background_migration --database assistant-dev.db
```

本轮新增覆盖：phase/status 分离、`next_attempt_at` 真实性、心跳续约、失去租约后不能
覆盖结果、weather_only 不产生 advisory、运行时按账号过滤、Nexus 运行按保存标识恢复、
Bark 真实路径记录与未知结果不重发、迁移副本重复执行、客户端进度契约。
接口与限制见 [runtime-contract.md](runtime-contract.md)。

真实 Bark 发送与手机收到尚未验证；真实 Apple 供应商不再作为当前通知前置条件，见
[real-provider-verification.md](real-provider-verification.md)。没有设备收到通知的证据前，
不应宣称端到端完成。

## 10. 后台运行验收（本轮）

- 单元测试：见最终交付记录（离线、隔离库、mock provider）。
- 真实 HTTP + 隔离后台进程：`scripts/commute_runtime_e2e.py` 会在临时库和随机端口启动
  API、scheduler、worker，验证受理/执行/进度/恢复/计划修改/账号隔离/结果保存，结束后
  终止全部进程；不发送真实 Bark/APNs，不调用真实模型。
- 原“本轮不启动新服务”表述改为“不新增外部基础服务，不操作共享运行实例”；隔离验收
  允许启动本轮专用进程。
