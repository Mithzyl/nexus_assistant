# Bark 按账号绑定

Bark 是服务器端通知渠道。账号归属只来自当前登录会话；App 提交 Key，后端从
`Authorization` 对应的 PA 用户写入数据库。不存在全局 owner，也不存在
`ASSISTANT_BARK_OWNER_USER_ID` / `ASSISTANT_BARK_DEVICE_KEY` 环境回退。

## 数据

`bark_reminder_settings`：

- `user_id`：当前登录 PA 用户；
- `device_key`：开发阶段**明文**存储。已知风险，不是生产级方案；后续加密。
- `auto_reminders_enabled`：默认 `false`；
- `assistant_notifications_enabled_at`：账号开启助手追问和到期提醒的时间，默认空。开启后只处理这个时间之后产生的通知；
- `last_test_status` / `last_test_at`。

Key 放在 Bark POST 请求体中，查询响应、日志和点击链接均不包含 Key。

## API

```text
GET    /api/v1/commute/bark
POST   /api/v1/commute/bark/binding   {"device_key": "..."}
DELETE /api/v1/commute/bark/binding
PATCH  /api/v1/commute/bark           {"auto_reminders_enabled": true|false}
POST   /api/v1/commute/bark/test      {"confirm_send": true}
```

- 保存 Key 不自动开启定时提醒，也不发送消息。
- 同一设置接口也接受 `{"assistant_notifications_enabled": true}`，可单独更新一个开关。返回状态包含 `web_links_available` 和 `assistant_notifications_enabled`。
- 替换 Key 覆盖当前账号的旧 Key。替换或解绑均关闭两个自动通知开关。
- 测试发送只在账号已绑定且显式确认时执行；自动提醒关闭也可以测试。
- 返回 `accepted` 只代表 Bark 接受 HTTP 请求，不代表手机收到。

## 点击打开 Web

Assistant 数据库需要通知相关迁移 `0033_bark_notifications` 和 `0035_commute_notification_stages`。在 Assistant 统一启动入口使用的环境文件中配置 `ASSISTANT_BARK_WEB_ORIGIN`，值为 iPhone 能访问的 Web HTTPS 源，例如 `https://assistant.example.com`。统一调度器生成通勤分阶段任务并复用现有发送队列。未配置地址时不会启动按阶段发送的通勤通知，助手追问和到期提醒暂不可开启。

| 后台触发条件 | Bark 点击地址 |
|---|---|
| 账号开启助手通知，助手保存仍待回复且未过期的追问 | `/ask?reply=<local_run_id>` |
| 账号开启通勤自动提醒，前晚预告、早间确认或行动有变 | `/me/commute/<plan_id>?service_date=YYYY-MM-DD` |
| 账号开启助手通知，待办或日程提醒到期 | `/today` |
| 已绑定设备并手动确认测试发送 | `/today` |

助手工作进程直接读取已保存的业务状态。待办与日程共用浏览器通知的到期与取消判断，无需浏览器订阅。工作进程恢复后补发最近 30 分钟内仍有效的提醒，且不早于账号开启时间。待办通知使用「待办提醒」作为标题，发送前从所属账号的待办读取当前标题作为正文，因此改名后会显示新名称。日程、独立提醒和助手追问继续使用通用正文。接收者未登录时，可以从目标页进入登录并回到原链接。页面继续按登录账号验证记录归属。

助手通知每个账号、每次业务发生只领取一次，用 `bark_notification_deliveries` 记录发出后的结果。连接中断或发送进程中止时保留未知状态，不自动重发。已有通勤通知继续记录在 `notification_outbox`。

## 通勤分阶段推送

按计划时区，每计划和服务日期分别允许前晚预告、早间确认和出行变更各一次。前晚默认 22:00 静默，可在计划中调整时间和通知级别。早间在计划检查时间刷新后发送。刷新失败时，仍有效的前晚方案会标明时效，无方案则提示暂未测出。默认每 10 分钟复查一次，若此前未发送有效出发时间，首次恢复立即占用变更名额。其余复查以最后一次被 Bark 接受的有效方案比较。出发提前至少 10 分钟、需要行动的新天气风险或主力线路变化会触发通知。

通知任务用已有路线和天气供应商完成确定性测算，保存日程、路线、通知快照和待发记录后，由 Worker 发送。前晚预告跨午夜失效，其他通知以建议出发或目标到达为截止。手动编辑可更新待发通知，关闭、跳过或过期时取消。已发送、明确失败或结果未知的槽位都不会自动重发。结果日志只记录计划编号、日期、阶段、状态和原因。

目前高德公交适配只使用已返回的上车线路和站点信息来组成文案。已预留首班、时刻表和实时预测字段，当前供应商不填这些时间。天气提示来自对应出行窗口的预报；推送不会携带详细地址或坐标。Web 接收有效服务日期；缺失、非法或重复时回到计划默认日期。

## 发送状态

| 状态 | outbox |
|---|---|
| Bark accepted | `accepted_by_bark` |
| Bark 明确失败 | `failed / bark_failed` |
| 超时或连接中断 | `unknown`，不重试 |
| 未绑定/自动提醒关闭 | `cancelled / bark_not_configured` 或 `bark_not_enabled` |

不启用 APNs 回退。APNs 代码保留但当前不可达。

## 使用入口

Web「我的 → Bark 提醒」可绑定、替换、解绑设备，分别启用通勤和助手通知，并确认发送测试提醒。iOS 原生设置继续支持原有通勤开关。

未知 Key、手机是否收到必须由用户真实验证；MockTransport 测试不能替代。
