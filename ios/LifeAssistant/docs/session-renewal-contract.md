# 会话续期 · 实际契约与联调状态

日期：2026-10-24（2026-10-24 晚按运行中 8010 OpenAPI 更新）
范围：`ios/LifeAssistant`；后端仓库未修改。

> 本文第 2 节保留为**历史提案，已被后端实际契约替代**，只用于对照，不再作为实现依据。

## 1. 实际契约（运行时 OpenAPI，8010）

- 路径：`POST /api/v1/auth/session/renew`（不是 `/auth/renew`、不是 `/auth/refresh`）。
- 请求：仅 `Authorization: Bearer <当前令牌>`，**无请求体**。
- 200 响应（`RenewResponse`）：

```json
{
  "user": { "user_id": "…", "email": "…", "display_name": "…" },
  "expires_at": "2026-10-31T12:00:00Z",
  "server_time": "2026-10-24T12:00:00Z",
  "token_rotated": false
}
```

- 语义：把同一条存活会话延长到 `server_time + TTL`；不轮换令牌、不复活已撤销会话、期限不回退。
- 错误码：`invalid_session`（401）、`session_expired`（401）、`session_revoked`（401）、
  `account_disabled`（403）。
- 登录响应同样带 `server_time`，客户端只取 `token/expires_at/user_id`，多余字段忽略。

## 2. 历史提案（已被替代，勿再实现）

`POST /api/v1/auth/renew` + `{token?, expires_at}` 的写法是 App 线程早期提案；
实际接口没有 `token` 字段，且明确不轮换。客户端已删除该假设。

## 3. 客户端行为（已按实际契约实现）

| 场景 | 行为 |
|---|---|
| 冷启动有令牌 | 先 `GET /auth/me`，再 `POST /auth/session/renew`；用响应里的 user 与 expires_at 恢复账号 |
| 回前台 | 统一入口、45 秒去抖、single-flight 合并并发调用；到期前 10 分钟再续；进后台取消定时 |
| 200 | 采纳 `expires_at`，账号以 `user` 为准；令牌原样保留，不重写 Keychain |
| `token_rotated: true` 但无新令牌 | 视为契约不一致：不采纳新期限、不改凭据，界面报错 |
| `invalid_session` / `session_expired` / `session_revoked` | 清理本机凭据、身份与期限，显示需重新登录 |
| `account_disabled` | 清理本机会话，提示“账号不可用”，不再自动续期 |
| 其他 401/403（含业务资源 403） | 保留凭据，只提示本次失败，不全局登出 |
| 404 | 兼容状态：“续期接口不可用”，保留凭据与原期限（不再是验收预期） |
| 断网/超时/5xx/429 | 保留令牌与期限，不把离线算作续期成功 |

## 4. 联调状态

- 运行时 OpenAPI 已包含该接口（29 条路径；OpenAPI SHA256 前缀 `1688bdc9cb49a990`）。
- 客户端路径、DTO 与错误码已对齐；单测覆盖 200、token_rotated、account_disabled、未知 403、404、离线、single-flight、退出丢弃迟到结果。
- 模拟器真实续期验收见交付报告（补充于运行验证之后）。
