# Bark 按账号绑定

Bark 是服务器端通知渠道。账号归属只来自当前登录会话；App 提交 Key，后端从
`Authorization` 对应的 PA 用户写入数据库。不存在全局 owner，也不存在
`ASSISTANT_BARK_OWNER_USER_ID` / `ASSISTANT_BARK_DEVICE_KEY` 环境回退。

## 数据

`bark_reminder_settings`：

- `user_id`：当前登录 PA 用户；
- `device_key`：开发阶段**明文**存储。已知风险，不是生产级方案；后续加密。
- `auto_reminders_enabled`：默认 `false`；
- `last_test_status` / `last_test_at`。

Key 不进入查询响应、日志、异常或 Git。状态响应只有是否绑定、是否开启和测试结果。

## API

```text
GET    /api/v1/commute/bark
POST   /api/v1/commute/bark/binding   {"device_key": "..."}
DELETE /api/v1/commute/bark/binding
PATCH  /api/v1/commute/bark           {"auto_reminders_enabled": true|false}
POST   /api/v1/commute/bark/test      {"confirm_send": true}
```

- 保存 Key 不自动开启定时提醒，也不发送消息。
- 替换 Key 覆盖当前账号的旧 Key；解绑清空 Key 并关闭自动提醒。
- 测试发送只在账号已绑定且显式确认时执行；自动提醒关闭也可以测试。
- 返回 `accepted` 只代表 Bark 接受 HTTP 请求，不代表手机收到。

## 发送状态

| 状态 | outbox |
|---|---|
| Bark accepted | `accepted_by_bark` |
| Bark 明确失败 | `failed / bark_failed` |
| 超时/连接中断/无法解析 | `unknown / bark_unknown`，不重试 |
| 未绑定/自动提醒关闭 | `cancelled / bark_not_configured` 或 `bark_not_enabled` |

不启用 APNs 回退。APNs 代码保留但当前不可达。

## App

“我的 → 通知设置”读取当前账号状态，提供保存/替换/解绑、自动提醒开关和
真实测试发送。Key 用 SecureField 提交，不在 App 持久化；重启后从后端恢复。

未知 Key、手机是否收到必须由用户真实验证；MockTransport 测试不能替代。
