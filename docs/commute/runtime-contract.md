# 通勤后台运行接口契约（2026-10-25）

本轮只补运行能力：独立 scheduler/worker、SQLite 持久化、心跳/租约、恢复与可观察进度。
不引入 APScheduler、Celery、Redis、RabbitMQ；API 进程内没有定时器。
Swift/Web 先按本文接入进度展示，再在后续轮次调整过期样式与历史区域。

## 1. 进程与隔离

```
App ──HTTP──> API (uvicorn) ──读写 SQLite；无定时器
                    ▲
Scheduler (独立进程) ─┘  生成窗口/入队天气或分析检查
Worker   (独立进程) ─┘  执行检查、保存快照/建议、Bark 出站
```

- API、scheduler、worker 可各自多实例；数据库条件 UPDATE 决定唯一处理者。
- 本轮验收可在隔离端口/临时库启动 API + scheduler + worker（及本地 Stub），
  结束后清理进程。这不等于部署新的生产服务。
- 生产上不新增外部基础服务，不操作共享 8010 实例或共享运行库。共享库迁移
  只在独立 SQLite 副本上验证。

## 2. Check 状态与阶段

`status` 表示任务结局，`phase` 表示当前步骤；两者分别定义，不得用一个布尔值
同时声称“程序在线”和“任务正常”。

| status | 含义 | 是否终态 |
|---|---|---|
| `queued` | 已受理，等待 worker 领取；`next_attempt_at` 非空仅表示真实重试时间 | 否 |
| `running` | 已被某个 worker 持有未过期租约 | 否 |
| `succeeded` | 完整执行且结果已保存 | 是 |
| `degraded` | 结果已保存但数据质量降级（如路线缺失） | 是 |
| `failed` | 执行失败，原因见 `error.code` | 是 |
| `cancelled` | 计划修改/关闭、跳过当天、到达时间已过等 | 是 |
| `unknown` | 结果确实未知，禁止伪造成功/失败 | 是 |

| phase | 含义 |
|---|---|
| `queued` | 尚未领取 |
| `fetching_route` | 正在请求服务端路线 |
| `fetching_weather` | 正在请求天气 |
| `analyzing` | 正在调用 Nexus AI 分析（仅 `full`） |
| `saving` | 正在保存结果，并校验处理资格 |
| `retry_wait` | 已安排真实重试，`next_attempt_at` 为可重试时间 |
| `done` | 结果保存完成且 status 不是 failed |
| `failed` | 结果保存完成但 status=failed |
| `cancelled` | 任务被取消 |

约束：

- `phase_updated_at` 是最近一次真实进度写入时间；客户端据此判断“任务最近进展”，
  与进程心跳 `last_seen_at` 分开显示。
- 终态 check 的 `next_attempt_at` 恒为 `null`；没有实际重试安排时不返回时间。
- 历史 check 的 `phase`、`analysis_mode`、`phase_updated_at` 保持 `null`，不回溯伪造。
- `error.code`、`error.retryable` 与 status 一致；`cancelled` 不标为 retryable。

## 3. analysis_mode 与 latest 语义

| analysis_mode | 行为 | advisory / latest |
|---|---|---|
| `full` | 路线 + 天气 + 可选 AI；保存建议 | 可能新建 advisory；`advisory_created=true` 仅表示本次新增 |
| `weather_only` | 只刷新天气快照 | 绝不新建 advisory；不改 latest；`advisory_id=null`、`advisory_created=false`；不调用模型 |
| `null` | 旧数据 | 不推断为 full/weather_only |

`weather_only` 下 `status=succeeded` 只表示天气已更新，不表示产生或更新通勤建议。
客户端应显示“天气已更新，建议未重新分析”，不得把旧建议的模型依据冒充本次结果。
定时天气刷新与定时 AI 分析分别由 `ASSISTANT_COMMUTE_AUTO_WEATHER_ENABLED`
（默认 true）和 `ASSISTANT_COMMUTE_AUTO_AI_ENABLED`（默认 false）控制；
启动 scheduler 不会自动开启收费分析。只有显式开启 AI 且路线 Provider 可用时，
定时检查才使用 `full`，否则使用 `weather_only`。

`GET /api/v1/commute-plans/{plan_id}/advisories/latest` 在 weather_only 后保持
原来的 404 或旧 advisory；客户端先读 check 的 `analysis_mode/advisory_created`
再决定展示。

## 4. 运行可用性接口

`GET /api/v1/commute/runtime` 需要登录；只统计当前账号的队列和最新 check。

```json
{
  "server_time": "...Z",
  "scheduler": {"state": "online|stale|not_seen", "last_seen_at": "...Z"},
  "worker":    {"state": "online|stale|not_seen", "last_seen_at": "...Z"},
  "stale_after_seconds": 60,
  "queue": {"queued": 0, "running": 1},
  "latest_check": {"check_id": "...", "status": "running",
                   "phase": "fetching_weather", "phase_updated_at": "...Z"},
  "last_progress_at": "...Z",
  "route_provider_available": true,
  "auto_analysis_enabled": false,
  "auto_analysis_mode": "weather_only"
}
```

- 不返回其他用户 ID、全局任务明细、scheduler/worker 的 instance_id、内部租约或密钥。
- `server_time` 是服务器当前时间，只回答“接口能否响应”；它与进程心跳、任务进度
  是三个独立信号，客户端用它换算 `last_seen_at` / `phase_updated_at` 的年龄。
- `scheduler/worker.last_seen_at` 是进程心跳；`latest_check.phase_updated_at` /
  `last_progress_at` 是任务进度。进程在线不等于任务在推进，反之亦然。
- 只有 API 进程在运行、scheduler/worker 都没启动时，接口仍返回 200 和
  `server_time`，但两个组件是 `not_seen`，排队任务保持 `queued` 且不会伪造结果。
- `stale` 阈值由 `ASSISTANT_COMMUTE_HEARTBEAT_STALE_SECONDS`（默认 60s）明确给出。
- 心跳由独立任务按 `ASSISTANT_COMMUTE_HEARTBEAT_INTERVAL_SECONDS`（默认 15s）更新，
  不等待当前长任务结束；worker 心跳同时按当前租约续期。
- 长任务期间（天气/路线/AI/Bark 网络等待）不持有 SQLite 写事务。结果保存用一条
  条件 UPDATE 校验 `status=running + lease_owner + 未过期租约`；失败者整事务回滚，
  旧执行者不能覆盖后来处理者的结果。

## 5. 恢复与未知结果

- 恢复已有 Nexus 运行先按已保存的 `nexus_run_id` 对账：有 workflow id 时读
  summary/result；只有 run id 时用同一幂等键重新挂接该 run，不创建第二个 run。
- 远端已受理、但本地尚未保存 run id 的崩溃窗口：本地只能标为
  `unknown`（`nexus_run_mapping_unknown`，AI 检查为 `ai_run_mapping_unknown`），
  不自动重发/新建。幂等键只能减少重复，不能证明该窗口安全；当前 App API 没有
  按幂等键反查 run 的入口，需要人工/上游补偿。
- check 租约过期后由其他 worker 重新领取是安全的：结果事务尚未提交则整体不可见。
- 推送不同：Bark 请求已发出、结果尚未落库时进程中断，结果无法判定。worker 会在
  租约过期后把 outbox 标为 `unknown`（`delivery_lease_expired_unknown`），不盲目重发；
  因此本地是 at-most-once，极端情况下可能漏一条，但不会因崩溃静默重复发送。

## 6. Bark 记录

- 只按 Bark 真实路径记录，不要求/创建 APNs `PushDevice`；`PushAttempt.device_id`
  为 `null`，`token_digest` 是 Bark key 的摘要。
- `accepted_by_bark` 只表示 Bark 接受请求，不等于手机送达。
- `failed` 表示明确拒绝/不可解析；`unknown` 表示超时、5xx 或结果不可判定。
- `unknown` 不自动重发；用户可重新触发测试或等待下一次计划提醒。

## 7. 兼容与迁移

- 新列 `analysis_mode/phase/phase_updated_at` 可空；旧 check/advisory 原样读取。
- `service_heartbeats` 为新增表。迁移只在副本上验证：先升级一次，再重复升级，
  两次都检查列/表/行数；共享库与 8010 实例本轮不动、不重启。
- OpenAPI 以 `docs/commute/openapi.json` 为准；本轮只增可选字段和枚举，不删除旧字段。
