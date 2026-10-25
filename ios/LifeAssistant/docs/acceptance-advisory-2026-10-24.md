# 已有建议只读展示与恢复 · 验收记录（2026-10-24 晚）

## 1. 数据与关联（真实 8010，非样例）

- 账号：`ui-a@example.com`（已授权测试账号），计划 `ba2169d9-76d5-4f3a-9e7a-36978320e319`（天府广场 → 成都东站，revision 2）。
- 关联（运行时 API + 只读 DB 双向核对）：
  - check `e8a8b733-5365-4cd1-91aa-d334f521cba4`（App 提交）→ advisory `d845e2d4-4d15-4fcc-9a57-9d6d1055380c`
  - check `1b62b34a-b679-4da2-95dd-27c38a51b713` → advisory `456485d2-055b-4391-b0f6-59e3d5a76fb6`
  - 计划最新建议当前即 `d845e2d4`；未用 latest 猜测其他 check 的结果。
- advisory `d845e2d4`：quality=degraded，`ai_analysis.status=available`，
  建议出发 21:54、地图/采用 26 分钟（1559 秒）、额外预留 10 分钟、有效期至 22:30。
- 运行时 OpenAPI 29 条路径，SHA256 `1688bdc9cb49a990d598cfb0a907427d8d3cf4d60619f207c1d29b5d3989d0f7`；
  `AdvisoryRead` 新增 `ai_analysis`（status/duration_source/used/map/extra/basis/missing_information）。

## 2. 本轮客户端改动

- `CommuteModels`：`AiAnalysis` DTO（只按字段名解释耗时来源，不写“含路况”）。
- `CommutePresentation`：`aiStatusText / aiDurationSourceText / aiMapDurationText / aiExtraBufferText /
  aiBasis / aiMissing / aiIsHistorical / aiTodayLine`；过期时一律标记为历史结果。
- `TodayCommuteCard`：显示分析状态、耗时来源、额外预留、缺失项数、天气获取时间与两条依据。
- `CommuteDetailViews`：新增“智能分析”卡（建议出发、耗时来源、实际采用、地图耗时、额外预留、
  依据、缺失信息、天气/路线获取时间），过期时显示“历史结果 + 不得当作当前出行”提示。
- `AppState`：新增 `init(demoMode: Bool? = nil)`，仅测试显式注入；正式运行仍只由 `PA_DEMO=1` 控制。
  `AppStateTests` 全部改为 `AppState(demoMode: true)`，`try XCTUnwrap` 取代强制解包，不再中断执行。
- 只读深链：`START_ADVISORY_ID` 注入 + 登录后打开指定 advisory（仅 GET，不触发 check/AI）。

## 3. 验证（同一最终构建，已签名 iPhone 18 Pro）

环境：Xcode 27.1（27A9275）、iOS 27.0（24A434）、UDID `F4F8159A-AB12-4106-8444-57AAA2D3FE96`，
签名 `CODE_SIGNING_ALLOWED=YES CODE_SIGNING_REQUIRED=YES CODE_SIGN_IDENTITY=-`。

| 项目 | 结果 | 说明 |
|---|---|---|
| 读取真实建议 | ✅ | Today 详情的建议出发、耗时来源、额外预留、依据、缺失信息、天气获取时间来自 `d845e2d4` |
| Today/详情一致 | ✅ | 断言两处标题文本一致（22:24 有效态；22:54 过期态） |
| 杀进程恢复 | ✅ | 重启后同一标题、同一详情，无新 check |
| 过期提示 | ✅ | 22:30 后 Today“时段已过”，详情“历史结果”+ 不得当作当前出行 |
| A/B 隔离 | ✅ | B 无计划与建议；切回 A 后恢复同一结果 |
| 只读（无 check/AI） | ✅ | DB：checks 5→5、advisories 5→5；测试窗口内新增 0；未点击“更新路线并分析” |
| 登录续期回归 | ✅ | `testLoginRestoreRenewThenLogoutDoesNotRevive` 通过（同一构建） |

## 4. 计数（实际执行，不是 error 行数）

- 单元测试：**111 executed / 111 passed / 0 failed / 0 skipped**（`xcodebuild test -only-testing:LifeAssistantTests`）。
- UI 只读用例：**1 executed / 1 passed**（`testReadOnlyAdvisoryConsistencyRestartAndIsolation`）。
- UI 登录续期用例：**1 executed / 1 passed**。
- 未执行/跳过：AI 建议生成（无 worker，后端 AI 未运行）、改到达时间的 UI 全链路；
  本轮明确不点击“更新路线并分析”，避免新增 AI run。

## 5. 截图

- `/tmp/detail_expired_final2.png`（最终构建，过期态详情）
- `/tmp/today_expired_final2.png`（最终构建，过期态 Today）
- `/tmp/detail_valid.png`（22:24 有效态详情，同一代码，仅 Today 文案后续微调）

## 6. 阻塞 / 风险

1. 后端没有 worker/scheduler 在运行，AI 结果不会再生成或更新；本轮只验证已存在的 available 结果。
2. “改到达时间后重新分析、旧结果不覆盖”仍只有契约/单测覆盖，未做 UI 操作（且会创建新 check）。
3. 未做真机验证；模拟器通过不等于真机通过。
