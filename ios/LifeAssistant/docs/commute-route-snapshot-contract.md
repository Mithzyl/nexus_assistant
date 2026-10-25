# 路线快照 · 实际契约与提交规则

日期：2026-10-24 晚（按运行中 8010 OpenAPI 更正）
范围：`ios/LifeAssistant`；后端未修改。

> 本文 2026-10-24 早先版本写“checks 不接受请求体”，已被运行时 OpenAPI 与新实现取代，
> 不再作为依据。下面的内容对应实际契约。

## 1. 实际请求

```http
POST /api/v1/commute-plans/{plan_id}/checks
Idempotency-Key: <uuid>
Content-Type: application/json

{
  "plan_revision": 3,
  "route_snapshot": {
    "provider": "mapkit_client",
    "origin": { "latitude": 30.6570, "longitude": 104.0657 },
    "destination": { "latitude": 30.6298, "longitude": 104.1412 },
    "transport_mode": "driving",
    "query_type": "arrival_deadline",
    "requested_arrival_at": "2026-10-25T01:00:00Z",
    "expected_departure_at": "…",
    "expected_arrival_at": "…",
    "queried_at": "…",
    "distance_meters": 8600,
    "duration_seconds": 1400,
    "duration_in_traffic_seconds": 1500,
    "eta_source": "calculate_eta",
    "eta_scope": "overall",
    "route_reference": "…",
    "candidate_routes": [
      { "route_reference": "…", "distance_meters": 8900, "duration_seconds": 1400 }
    ],
    "source_note": "本机 MapKit 查询结果，非 Apple 服务端签名。"
  }
}
```

- `plan_revision` 与当前计划不一致时返回 409 `plan_revision_conflict`。
- 快照起点/终点必须与计划坐标相差 ≤0.01°；出行方式必须一致；
  `queried_at` 必须在服务端 now-15min ～ now+2min；`query_type` 与 requested 字段不能互相矛盾。
- `arrival_deadline` 查询只能带 `requested_arrival_at`，不能同时带出发时间。
- 同一幂等键 + 不同 body 返回 409 `idempotency_key_reused`。
- 响应 `CheckCreated` 只含 `check_id/status`，不回显快照；快照与 hash 存在服务端检查记录里。

## 2. MapKit 事实口径

- 路线几何/候选：`MKDirections.calculate()`；候选各自只提交自己的 `duration_seconds`。
- 交通耗时：`MKDirections.calculateETA()` 的 `ETAResponse.expectedTravelTime`，
  作为总体 `duration_in_traffic_seconds`（`eta_source=calculate_eta`, `eta_scope=overall`）。
- `MKRoute.expectedTravelTime` 是路线静态估算，只作为 `duration_seconds`，不冒充交通 ETA。
- 驾车按计划目标到达时间设置 `arrivalDate`；步行是静态耗时，不按到达时间推算。

## 3. 客户端提交规则（已实现）

- 一次用户操作生成一份 body 和一个幂等键；超时/断网时保存 body+键+账号，
  下次“核对上次提交”原样复用，服务器按幂等键重放，不新建分析。
- 结果未知时禁止静默重新分析；重启后从本地按账号恢复待核对记录。
- `plan_revision_conflict`：重新读取计划，按新 revision 重新查询路线并创建新提交；
  最多自动重试一次，失败后停止并提示。
- `idempotency_key_reused`：不自动换键重试，明确报冲突。
- `route_snapshot_*` 422：清除待核对记录并提示重新查询路线。
- 路线查询失败：不提交快照，只允许服务器按计划生成天气/无路线降级建议。

## 4. 契约证据

- 运行时 OpenAPI：`CheckCreateRequest`、`RouteSnapshotInput`、`RouteCandidateInput`、`RoutePoint`；
  OpenAPI SHA256 前缀 `1688bdc9cb49a990`（8010，29 条路径）。
- 后端实现校验见 `commute/services/checks.py`（本次只读核对，未修改）。
