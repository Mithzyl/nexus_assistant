# 客户端验收记录 · 会话续期与 MapKit 通勤（2026-10-24）

## 1. 环境与签名

- 后端：`http://127.0.0.1:8010`（App Info.plist `PAApiBaseURL` 与 UI 测试 `PA_API_BASE_URL` 一致；问答/认证/通勤同一实例）。
- 运行时 OpenAPI：29 条路径，SHA256 `1688bdc9cb49a990d598cfb0a907427d8d3cf4d60619f207c1d29b5d3989d0f7`。
- 模拟器：iPhone 18 Pro，UDID `F4F8159A-AB12-4106-8444-57AAA2D3FE96`，iOS 27.0（`com.apple.CoreSimulator.SimRuntime.iOS-27-0`，24A434）。
- Xcode：27.1（27A9275）。
- 签名：`CODE_SIGNING_ALLOWED=YES CODE_SIGNING_REQUIRED=YES CODE_SIGN_IDENTITY=-`，保留
  `keychain-access-groups = com.mith.lifeassistant`；未使用 `CODE_SIGNING_ALLOWED=NO`。

## 2. 契约核对

- 续期：`POST /api/v1/auth/session/renew`，仅 Bearer、无 body；响应 `{user, expires_at, server_time, token_rotated}`。
- checks：`POST /api/v1/commute-plans/{id}/checks` 接受 `{plan_revision, route_snapshot}`；
  `RouteSnapshotInput` 必填 provider/origin/destination/transport_mode/query_type/queried_at。
- 已用真实请求验证：202 建检查；同键同 body 重放同一个 check；同键不同 body → 409
  `idempotency_key_reused`；旧 `plan_revision` → 409 `plan_revision_conflict`。

## 3. 结果矩阵

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| 1 | 登录保存期限、杀进程重开恢复 | ✅ | UI `testLoginRestoreRenewThenLogoutDoesNotRevive` 106.8s；`account.expiry` 重启后仍在 |
| 2 | 前后台真实续期、采纳服务端新期限 | ✅ | 同用例 `account.renewed`；DB 会话 `expires_at` 被延长 |
| 3 | 断网保留凭据、联网恢复；退出后不复活 | ✅ | `testOfflineKeepsCredentialAndRecovers` 57.6s；logout 先清本地再撤销 |
| 4 | A/B 切号不串数据 | ✅ | `testAccountSwitchDoesNotLeakPlan` 127.4s |
| 5 | 真实 MapKit 路线与 calculateETA | ✅ | `testMapKitRouteAndSnapshotSubmission` 76.5s；26 分钟、8931 米、3 条候选、查询 20:59 |
| 6 | 提交快照，后端保存快照与计划版本 | ✅ | 202；`commute_checks` 行：`plan_revision=2`、`provider=mapkit_client`、`route_snapshot_hash=5d9f04a52ad0…`、`query_type=arrival_deadline` |
| 7 | 后端 AI 完成后展示 available 建议；Today 与详情一致 | ⏳ 等待后端 | 无 worker/scheduler 进程；后端 `_ai_analysis_for_check` 当前返回 `ai_analysis_unavailable` |
| 8 | 改到达时间后重新查询，旧结果不覆盖 | ⚠️ 部分 | 409 冲突与“重读计划→重查路线→新键新 body”已由单测覆盖；未做改时间 UI 全链路 |
| 9 | 重启恢复服务端保存的建议 | ⏳ 等待后端 | 依赖 #7；无建议可取 |

## 4. 真实提交与模型预算

- 真实检查提交 2 次：1 次 curl 契约探测、1 次 App UI 全链路；两次均 `queued`，因为
  worker 未运行，**未产生模型调用**（上限 3 次未用完）。
- 未使用 Fake 成功补齐真实验收；AI 部分如实标记等待后端。

## 5. 单元测试

- 相关模块（续期、会话、MapKit 提交、通勤 Store/Presentation、Ask、Markdown）：
  `65/65` 通过。
- 全量 `LifeAssistantTests`：96 条，`AppStateTests` 12 条失败（演示数据要求 `PA_DEMO=1`，
  单测 scheme 未注入），与本轮改动无关，未修改该线程文件。

## 6. 客户端改动（本轮）

- 续期契约：`PA/PAModels.swift`（RenewResponse）、`PA/PAApiClient.swift`（真实路径、logout 带原令牌）、
  `PA/PASession.swift`（期限/错误码/前台续期/先清本地再撤销）、`PA/SessionRenewal.swift`、`App.swift`、`Screens/MeView.swift`。
- 快照提交：`Commute/CommuteCheckSubmission.swift`（真实 DTO、PendingSubmission）、
  `Commute/CommuteAPI.swift`、`Commute/CommuteRepository.swift`、`Commute/CommuteStore.swift`（提交/核对/冲突/持久化）、
  `Commute/CommuteDetailViews.swift`、`Commute/CommuteIntents.swift`。
- 测试：`LifeAssistantTests/SessionRenewalTests.swift`、`CommuteRouteTests.swift`、`PASessionTests.swift`、
  `CommuteStoreTests.swift`；UI `LifeAssistantUITests/SessionCommuteAcceptanceUITests.swift`。
- 文档：`docs/session-renewal-contract.md`、`docs/commute-route-snapshot-contract.md`。

## 7. 剩余阻塞

1. 后端 worker/scheduler 未运行，且 AI 分析接口未实现 → #7、#9 无法真实验收。
2. 改到达时间的 UI 全链路（#8）只做了契约与单测，未在界面操作。
3. 未做真机验证；模拟器通过不等于真机通过。
