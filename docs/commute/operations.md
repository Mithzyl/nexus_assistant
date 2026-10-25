# 部署与任务配置

## 1. 进程拓扑

```
App ──HTTPS──> API (uvicorn, N 个 worker)
                    │  只读写数据库；没有定时器
                    ▼
                   SQLite
                    ▲
Scheduler (1..N，租约保护) ── 生成实例/入队检查
Worker   (1..N，条件抢占) ── 执行检查、生成建议、发送 Bark
```

- API 可以水平扩容，不会重复调度。
- Scheduler 可以起多个，`commute_leases` 只允许一个持有者；即使租约重叠，
  数据库唯一键仍会去重。
- Worker 可以起多个，`commute_checks` / `notification_outbox` 通过条件 UPDATE 抢占。
- 不需要 Redis；若后续引入，只能做缓存/限流加速，不能替代数据库幂等键。
- 定时天气刷新（`ASSISTANT_COMMUTE_AUTO_WEATHER_ENABLED=true`）与定时 AI 分析
  （`ASSISTANT_COMMUTE_AUTO_AI_ENABLED=false`）分别控制。默认不会在启动后自动
  开启收费分析；测试开启也只对隔离库生效。
- AI 未开启或路线 Provider 不可用时，定时检查使用 `weather_only`：只保存天气，
  不创建/修改 advisory、不调用模型；App 手动检查仍为 `full`。
- 进程心跳独立于长任务更新；worker 心跳同时续期当前 check/outbox 租约。过期阈值
  由 `ASSISTANT_COMMUTE_HEARTBEAT_STALE_SECONDS` 明确配置，默认 60s。
- 结果保存必须通过当前租约条件 UPDATE；失去资格的旧执行者整事务回滚，不能覆盖
  后来处理者的结果。网络等待期间不持有 SQLite 写事务。
- 接口与客户端语义见 [runtime-contract.md](runtime-contract.md)。

## 2. systemd 示例

`/etc/systemd/system/assistant-api.service`：

```ini
[Unit]
Description=Personal Assistant API
After=network-online.target

[Service]
Type=simple
User=assistant
WorkingDirectory=/srv/assistant-api
EnvironmentFile=/etc/assistant/assistant.env
ExecStart=/srv/assistant-api/.venv/bin/uvicorn assistant_api.main:app --host 127.0.0.1 --port 8010 --workers 4
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

`assistant-commute-scheduler.service`：

```ini
[Service]
Type=simple
User=assistant
WorkingDirectory=/srv/assistant-api
EnvironmentFile=/etc/assistant/assistant.env
Environment=ASSISTANT_ENVIRONMENT=production
Environment=ASSISTANT_COMMUTE_PROVIDER_MODE=live
ExecStart=/srv/assistant-api/.venv/bin/python -m assistant_api.commute.scheduler
Restart=always
RestartSec=5
```

`assistant-commute-worker.service`：

```ini
[Service]
Type=simple
User=assistant
WorkingDirectory=/srv/assistant-api
EnvironmentFile=/etc/assistant/assistant.env
Environment=ASSISTANT_ENVIRONMENT=production
Environment=ASSISTANT_COMMUTE_PROVIDER_MODE=live
ExecStart=/srv/assistant-api/.venv/bin/python -m assistant_api.commute.worker
Restart=always
RestartSec=5
```

不要把 scheduler/worker 放在 API 进程内，也不要用 API 的 `--reload` 在运行时轮询任务。

## 3. 数据库

当前项目使用 SQLite + SQLAlchemy/aiosqlite。

```bash
cd services/assistant-api
.venv/bin/python -m scripts.migrate_sqlite init      # 空库
.venv/bin/python -m scripts.migrate_sqlite upgrade   # 既有库增量
.venv/bin/python -m scripts.migrate_sqlite status    # 已应用版本
```

- 增量迁移只添加缺失列，执行前检查 `PRAGMA table_info`；重复执行会记录
  `already_present`，不会吞掉其他 SQL 错误。
- 先备份数据库；迁移失败立即停止并恢复备份，不把失败标记为成功。
- 正式运行设置 `ASSISTANT_ALLOW_SCHEMA_CREATE=false`。
- 每日清理（cron 示例，低峰执行）：

```cron
15 3 * * * cd /srv/assistant-api && .venv/bin/python -m assistant_api.commute.cleanup --days 30 >> /var/log/assistant-cleanup.log 2>&1
```

清理只删除过期执行历史；计划删除会在 API 事务中立即清理子数据。

## 4. 后台运行与恢复检查

```bash
# 独立进程（均支持 --once）
.venv/bin/python -m assistant_api.commute.scheduler
.venv/bin/python -m assistant_api.commute.worker
.venv/bin/python -m assistant_api.commute.cleanup --days 30

# 运行可用性（需登录；只返回当前账号的队列/最新进度）
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8010/api/v1/commute/runtime
```

- 恢复已有 Nexus 运行先按保存的 `nexus_run_id` 对账；只有本地幂等键、没有保存
  run id 的崩溃窗口标为 `unknown`，不新建第二个 run。
- Bark 请求已发出但结果尚未落库时，租约过期后 outbox 标为
  `unknown(delivery_lease_expired_unknown)`，不盲目重发；`accepted_by_bark` 不等于送达。
- 运行库/共享 8010 本轮不改动、不重启；迁移先在独立副本验证并重复执行：

```bash
.venv/bin/python -m scripts.verify_background_migration --database assistant-dev.db
```

## 5. 配置检查

生产启动时 `commute_configuration_errors` 会拒绝 `ASSISTANT_COMMUTE_PROVIDER_MODE`
不是 `live` 的配置。Bark 按登录账号在 App 内绑定，不要求全局 owner/key；
Apple WeatherKit/Maps/APNs 凭证不是启动前置。缺失能力按 unavailable 返回，
不会静默生成 mock 数据，也不会假装推送成功。
密钥文件建议权限 `0400`，属主为服务账号；不要写入仓库、镜像层、日志或 App。

## 6. 监控建议（不输出地点）

| 指标 | 建议告警 |
|---|---|
| `commute_checks` 按 status 计数 | `failed`/`cancelled` 异常升高 |
| 检查排队时长（created_at → finished_at） | 超过 5 分钟 |
| `notification_outbox` pending 数 | 持续增长（worker 停止/Bark 故障） |
| `push_attempts` error_code | `apns_auth_failed`、`BadDeviceToken` 异常升高 |
| scheduler lease 持有时间 | 长期无更新（scheduler 停止） |
| `commute_advisories.quality` | degraded/unavailable 比例异常 |

日志只输出 plan/check/outbox id，不输出坐标、地点名称、APNs token 或密钥。

## 7. 发布与回滚

1. 备份数据库。
2. 执行迁移（只新增表/索引，不改现有表）。
3. 部署 API（可先于 scheduler/worker）。
4. 部署 scheduler/worker，观察一个执行日。
5. 回滚代码不需要删表；表可保留，旧代码不读取。若要回滚迁移，先确认没有
   `commute_advisory` 通知在途，再按依赖顺序 drop 子表。

## 8. 已知运行限制

- Bark accepted 不等于送达；超时/不可解析标记 unknown，不自动重发或切换 APNs。
- 发送成功但结果提交前进程中断无法判定；本地按 unknown 收敛（at-most-once），
  可能漏发，但不会把未知当成功或静默重复发送。
- 远端已受理但本地 run id 未落库的窗口无上游反查接口，只能显式 unknown。
- 第一版只支持同日本地自然日、driving/walking、固定地点。
- 服务器建议出发时间是 ETA + 预留的估算，不是导航实时值，也不承诺准时。
- 修改计划不会撤回已接受的推送；未发送 outbox 会取消。
