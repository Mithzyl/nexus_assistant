# App 联调清单（通勤提醒）

目标：App 只负责设置与交互；天气、ETA、建议出发时间、通知全部以服务端为准。
App 不得自行用 MapKit 计算正式 ETA，也不得在本地补造缺失的出发时间/降雨概率。

调试说明：App **不要求每次调试都连接服务器**。本地 UI 调试可设 `COMMUTE_DEMO=1`（仅 DEBUG，
使用 `DemoCommute`）；或使用上次成功的本地快照。`COMMUTE_DEMO` 不会在 Release 生效。
远端联调只用于验证真实 DTO 与交互，不作为日常开发前置条件。

## 1. 契约与身份

- [ ] 以 `docs/commute/openapi.json` 生成或核对 DTO；字段全部 snake_case，Swift 显式 CodingKeys。
- [ ] API base 为 `/api/v1`；携带现有 Bearer 会话，不发送 `user_id`。
- [ ] 识别所有时间戳为 UTC `Z`，用 ISO8601 解析；显示时按计划 `timezone` 转本地。
- [ ] `weekdays` 用 ISO 1=周一 … 7=周日；App 内选择器不要用 `Calendar` 的 1=周日直接映射。
- [ ] 时间输入严格 `HH:mm`；`check_local_time < arrival_local_time`，跨午夜给出“暂不支持”提示。
- [ ] 坐标 WGS84；`latitude [-90,90]`、`longitude [-180,180]`。
- [ ] 统一处理错误 envelope：`error.code/message/retryable` + `request_id`；不要把 message 当分支条件。
- [ ] 对 428（缺 If-Match）、409（revision 冲突）、404、422、503 分别有 UI 行为。

## 2. 计划设置

- [ ] 保存计划时记录响应 `ETag` / `revision`；更新 PUT 必须带 `If-Match: "<revision>"`。
- [ ] 收到 409 时重新 GET 再合并，不要覆盖服务端新版本。
- [ ] 删除计划后清空本地缓存的地点/建议；服务端会立即删除子数据并停止调度。
- [ ] 地点标签只在设置页显示，不写入通知文案假设。
- [ ] 切换交通方式只有 driving/walking；不支持的地区由服务端返回不可用，不自动切换。

## 3. 手动刷新

- [ ] POST checks 必须生成并持久化 `Idempotency-Key`（至少 8 字符，建议 UUID），重试复用同一个 key。
- [ ] 202 后用 `check_id` 轮询 `GET /checks/{id}`；终态为 succeeded/degraded/failed/cancelled。
- [ ] 手动刷新默认不推送；UI 不得暗示“已发通知”。
- [ ] `failed` 时展示 `error.message` 和重试建议；`degraded` 时展示已知限制。
- [ ] 没有建议时处理 404 `NO_ADVISORY`，不要显示空白卡片。

## 4. 建议展示

- [ ] `quality`: fresh/degraded/unavailable；`departure_status`: upcoming/leave_now/past_due/unknown。
- [ ] `recommended_departure_at == null` 时显示“暂时无法给出精确出发时间”，不推断。
- [ ] `weather == null` 或概率 `null` 时显示“天气暂不可用”，**不能显示 0%**。
- [ ] `eta_seconds == null` 时隐藏精确耗时/出发时间。
- [ ] `sources` 展示 provider、抓取时间、有效时间、freshness；Apple Weather 的 attribution
      文案与链接按返回展示，不能删除。
- [ ] 文案以服务端 `title/summary` 为准；App 不做数字改写或补全。
- [ ] `departure_status=past_due` 可展示“现在出发可能偏晚”，但不得声称已经迟到。
- [ ] 过期建议（`expires_at` 已过）显示为历史，不用它继续提醒。

## 5. 地图与导航

- [ ] 地图/地点搜索使用 MapKit 原生能力。
- [ ] “打开导航”用 MapKit 打开 origin → destination，坐标来自计划接口；不要把服务端 ETA 当作导航 ETA。
- [ ] 若需要展示服务端 ETA，明确标注“服务端估算”，与 MapKit 当前路线耗时区分。

## 6. 推送注册与账号

- [ ] 登录成功后、获得通知权限后调用 `PUT /push-devices/{installation_id}`。
- [ ] `installation_id` 使用 `identifierForVendor` 或 Keychain 持久 UUID，不是用户身份。
- [ ] APNs token 变化时立即重新 PUT；`notification_permission` 使用 authorized/provisional/
      ephemeral/denied/unknown 之一。
- [ ] 退出登录先调用 logout（服务端按 session 删绑定），再清理本地状态；切换账号必须走 logout，
      否则同安装实例的另一活跃账号会返回 409 `installation_bound_other_user`。
- [ ] 不要尝试在客户端指定 APNs topic 或 sandbox/production；服务端配置为准。
- [ ] 推送权限被拒绝时，计划仍可手动刷新，但服务端不会伪造发送成功。

## 7. 通知处理

Payload 业务字段：

```json
{
  "aps": {"alert": {"title": "建议 08:20 出发", "body": "..."}, "sound": "default"},
  "type": "commute_advisory",
  "advisory_id": "9ab3...",
  "plan_id": "c0a8012e-...",
  "local_service_date": "2026-10-23",
  "schema_version": 1
}
```

- [ ] 仅处理 `type == "commute_advisory"`；未知 `schema_version` 安全忽略。
- [ ] 点击后 GET `/advisories/{advisory_id}` 再展示详情；不要只依赖 payload 内容。
- [ ] 点击后上报 `POST /advisories/{id}/actions {action:"opened"}`，带 `Idempotency-Key`。
- [ ] “今天跳过”上报 `skip_today`；服务端停止当天自动检查和未发送通知。
- [ ] App 不应把 `opened` 当作服务端送达凭据；APNs accepted 也不等于用户收到。
- [ ] 通知正文默认不含精确家庭地址；不要在自定义字段中补发地址/坐标。
- [ ] 通知可能重复/丢失/延迟；以打开 App 后拉取的最新建议为准。

## 8. 测试场景（真机前）

- [ ] 固定时钟/后端 mock 下，创建计划 → 手动刷新 → 展示建议。
- [ ] 时间戳显示跨时区（如计划 Asia/Shanghai、设备 America/New_York）正确。
- [ ] 422/409/428/404/503 的 UI 不崩溃、不丢输入。
- [ ] 删除计划后下拉列表不残留旧建议；账号切换后不再收到旧账号通知。
- [ ] 设备 token 轮换后旧 token 的 410 不删除新 token（服务端负责，App 只需重注册）。
- [ ] 真机：APNs sandbox 通知点击 → advisory 详情 → MapKit 导航。
- [ ] 记录实际收到通知的时间、设备型号、iOS 版本、APNs 环境与截图；未收到不算通过。
