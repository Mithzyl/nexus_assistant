# 通勤提醒接口示例

所有示例假定：`BASE=http://127.0.0.1:8010`、`TOKEN` 为现有登录接口签发的本地会话令牌。
`user_id` 从不出现在请求体里。时间戳响应均为 UTC `Z`。

```bash
BASE=http://127.0.0.1:8010
TOKEN='pas_...'
AUTH="Authorization: Bearer $TOKEN"
```

## 1. 创建计划（合法）

```bash
curl -sS -X POST "$BASE/api/v1/commute-plans" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{
    "name": "工作日上午通勤",
    "enabled": true,
    "timezone": "Asia/Shanghai",
    "weekdays": [1,2,3,4,5],
    "arrival_local_time": "09:00",
    "check_local_time": "07:30",
    "transport_mode": "driving",
    "buffer_minutes": 10,
    "rain_probability_threshold": 0.4,
    "rain_extra_buffer_minutes": 10,
    "notify_enabled": true,
    "origin": {"label": "家", "latitude": 31.2304, "longitude": 121.4737},
    "destination": {"label": "公司", "latitude": 31.2200, "longitude": 121.4500}
  }'
```

`201 Created`，`ETag: "1"`：

```json
{
  "id": "c0a8012e-...",
  "revision": 1,
  "created_at": "2026-10-23T05:00:00Z",
  "updated_at": "2026-10-23T05:00:00Z",
  "name": "工作日上午通勤",
  "enabled": true,
  "timezone": "Asia/Shanghai",
  "weekdays": [1, 2, 3, 4, 5],
  "arrival_local_time": "09:00",
  "check_local_time": "07:30",
  "transport_mode": "driving",
  "buffer_minutes": 10,
  "rain_probability_threshold": 0.4,
  "rain_extra_buffer_minutes": 10,
  "notify_enabled": true,
  "origin": {"label": "家", "latitude": 31.2304, "longitude": 121.4737},
  "destination": {"label": "公司", "latitude": 31.22, "longitude": 121.45}
}
```

## 2. 非法创建（跨午夜 / 越界 / 伪造 user_id）

跨午夜：`check_local_time` 必须早于 `arrival_local_time`。

```bash
curl -sS -X POST "$BASE/api/v1/commute-plans" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"夜班","timezone":"Asia/Shanghai","weekdays":[1],"arrival_local_time":"07:00","check_local_time":"23:30","transport_mode":"walking","origin":{"label":"a","latitude":31.2,"longitude":121.4},"destination":{"label":"b","latitude":31.3,"longitude":121.5}}'
```

`422`：

```json
{
  "error": {"code": "invalid_request", "message": "请求字段格式或取值无效。", "retryable": false},
  "request_id": "7a3f9c1e-..."
}
```

其它非法示例：
- `latitude: 91` / `longitude: 181` → 422
- `rain_probability_threshold: 1.5` → 422
- `weekdays: [0, 8]` → 422
- 请求体带 `"user_id": "..."` → 422（`extra="forbid"`）
- `transport_mode: "transit"` → 422（第一版只开放 driving/walking）

## 3. 完整更新与 revision 冲突

缺少 `If-Match`：

```bash
curl -sS -X PUT "$BASE/api/v1/commute-plans/$PLAN_ID" \
  -H "$AUTH" -H 'Content-Type: application/json' -d @plan.json
# 428 precondition_required
```

携带旧 revision：

```bash
curl -sS -X PUT "$BASE/api/v1/commute-plans/$PLAN_ID" \
  -H "$AUTH" -H 'If-Match: "1"' \
  -H 'Content-Type: application/json' -d @plan.json
# 若当前已是 2 => 409 revision_conflict
```

正确：

```bash
curl -sS -X PUT "$BASE/api/v1/commute-plans/$PLAN_ID" \
  -H "$AUTH" -H 'If-Match: "2"' \
  -H 'Content-Type: application/json' -d @plan.json
# 200 + ETag: "3"
```

`If-Match` 也接受 `W/"2"` 和 `2`；更新会取消旧 revision 的排队任务与未发送 outbox，
已发送通知不撤回。

## 4. 手动刷新（202 + 幂等）

```bash
curl -sS -X POST "$BASE/api/v1/commute-plans/$PLAN_ID/checks" \
  -H "$AUTH" -H 'Idempotency-Key: manual-refresh-20261023-0730'
```

`202 Accepted`：

```json
{"check_id": "4a44dc2e-...", "status": "queued"}
```

重复同一个 key 返回同一 `check_id`，响应头 `X-Idempotent-Replay: true`。手动刷新不推送、
不消耗每日自动提醒次数。缺少 `Idempotency-Key` → 422。

查询：

```bash
curl -sS "$BASE/api/v1/checks/$CHECK_ID" -H "$AUTH"
```

```json
{
  "check_id": "4a44dc2e-...",
  "plan_id": "c0a8012e-...",
  "status": "succeeded",
  "trigger": "manual",
  "plan_revision": 1,
  "advisory_id": "9ab3...",
  "advisory_created": true,
  "error": null,
  "created_at": "2026-10-23T23:30:00Z",
  "finished_at": "2026-10-23T23:30:04Z"
}
```

失败示例：

```json
{
  "check_id": "...",
  "status": "failed",
  "error": {
    "code": "providers_unavailable",
    "message": "天气和路线数据均不可用。",
    "retryable": false
  }
}
```

## 5. 最新建议

```bash
curl -sS "$BASE/api/v1/commute-plans/$PLAN_ID/advisories/latest" -H "$AUTH"
```

没有建议时 `404`：

```json
{"error": {"code": "NO_ADVISORY", "message": "暂无通勤建议。", "retryable": false}, "request_id": "..."}
```

成功示例（天气/ETA 均可用，降雨阈值触发）：

```json
{
  "id": "9ab3...",
  "plan_id": "c0a8012e-...",
  "plan_revision": 1,
  "local_service_date": "2026-10-24",
  "generated_at": "2026-10-23T23:30:04Z",
  "expires_at": "2026-10-24T01:00:00Z",
  "quality": "fresh",
  "title": "建议 08:20 出发",
  "summary": "建议 08:20 出发，驾车约 30 分钟，基础预留 10 分钟，计划于 09:00 前到达；降雨概率最高约 20%。",
  "target_arrival_at": "2026-10-24T01:00:00Z",
  "recommended_departure_at": "2026-10-24T00:20:00Z",
  "departure_status": "upcoming",
  "eta_seconds": 1800,
  "buffer_seconds": 600,
  "weather_extra_buffer_seconds": 0,
  "weather": {
    "forecast_start": "2026-10-24T00:10:00Z",
    "forecast_end": "2026-10-24T01:00:00Z",
    "temperature_c": 18.0,
    "precipitation_probability_max": 0.2,
    "precipitation_probability_known": true,
    "rain_probability_threshold": 0.4,
    "rain_risk": false,
    "coverage": "full",
    "locations": [
      {"role": "origin", "coverage": "full", "max_probability": 0.2},
      {"role": "destination", "coverage": "full", "max_probability": 0.2}
    ]
  },
  "sources": [
    {
      "kind": "weather",
      "provider": "weatherkit",
      "fetched_at": "2026-10-23T23:30:02Z",
      "valid_from": "2026-10-23T00:00:00Z",
      "valid_to": "2026-10-24T08:00:00Z",
      "freshness": "fresh",
      "attribution": {
        "text": "天气数据由 Apple Weather 提供",
        "url": "https://developer.apple.com/weatherkit/data-source-attribution/"
      },
      "note": "按出发地/目的地相关预报时段估算，不代表整条道路逐点天气。"
    },
    {
      "kind": "route",
      "provider": "apple_maps_server_api",
      "fetched_at": "2026-10-23T23:30:01Z",
      "valid_from": null,
      "valid_to": "2026-10-24T01:00:00Z",
      "freshness": "fresh",
      "attribution": {
        "text": "路线与耗时数据由 Apple Maps Server API 提供",
        "url": "https://developer.apple.com/documentation/applemapsserverapi"
      },
      "request_depart_at": "2026-10-23T23:50:00Z",
      "target_arrival_at": "2026-10-24T01:00:00Z",
      "note": "ETA 请求使用计划中的未来时间基准，不是无条件使用“现在出发”。"
    }
  ],
  "reason_codes": [
    "ROUTE_FRESH",
    "WEATHER_FRESH",
    "WEATHER_RAIN_THRESHOLD_NOT_MET",
    "DEPARTURE_UPCOMING"
  ],
  "explanation_source": "template"
}
```

注意：
- `recommended_departure_at` / `eta_seconds` 可以为 `null`，App 不补造。
- 概率缺失为 `null` 且 `precipitation_probability_known=false`，不能显示成 0%。
- 建议详情不包含计划地点名称或坐标。

## 6. 注册推送设备

```bash
curl -sS -X PUT "$BASE/api/v1/push-devices/5D4E2C1A-..." \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"apns_token":"aabbccddeeff...","notification_permission":"authorized"}'
```

`200`：

```json
{
  "installation_id": "5D4E2C1A-...",
  "notification_permission": "authorized",
  "environment": "sandbox",
  "updated_at": "2026-10-23T23:20:00Z"
}
```

非法（客户端试图指定环境）：

```json
{"apns_token":"...","notification_permission":"authorized","environment":"production"}
```

→ 422 `invalid_request`。topic/environment 由服务端配置决定。

## 7. 通知动作

```bash
curl -sS -X POST "$BASE/api/v1/advisories/$ADVISORY_ID/actions" \
  -H "$AUTH" -H 'Idempotency-Key: open-20261023-0820' \
  -H 'Content-Type: application/json' \
  -d '{"action":"opened","installation_id":"5D4E2C1A-..."}'
```

```json
{"advisory_id":"9ab3...","action":"opened","recorded_at":"2026-10-24T00:21:00Z","replayed":false}
```

`skip_today` 相同，但会关闭当天实例、取消排队检查和未发送 outbox。`opened` 只表示 App 上报打开，
不是 APNs 送达或用户阅读凭证。

同一 `Idempotency-Key` 换一个请求体：

```json
{"error":{"code":"idempotency_key_reused","message":"同一 Idempotency-Key 不能用于不同请求。","retryable":false},"request_id":"..."}
```

## 8. 越权示例

用户 B 读取用户 A 的计划/检查/建议/设备动作，统一返回 404（不泄露资源是否存在）：
`plan_not_found` / `check_not_found` / `advisory_not_found`。使用未绑定到当前账号的
`installation_id` 上报动作返回 403 `device_not_bound`。
