# 通勤状态表达与历史建议 · 验收记录（2026-10-25）

## 1. 本轮改动（仅 `ios/LifeAssistant`）

- `CommuteStore`：
  - 新增 `historicalAdvisory` / `openedAdvisory` / `planUpdatedNotice`；
    `isApplicable(advisory:plan:)` 按“计划版本一致 + 未过期 + 服务日不早于今天”判定。
  - 旧版本或过期响应只进历史区，不能覆盖当前建议；`refresh()` 改为只读
    （只 GET 计划与最新建议，不创建 check）；创建分析只保留 `updateRouteAndAnalyze()`。
  - 保存新 revision 后：旧建议转历史、清空待核对提交（旧快照/旧键不得复用）并提示
    “旧建议不适用于新计划”。
  - `processingStateText` 只使用后端真实 check 状态与待核对提交（带提交时间），不画百分比。
- `CommutePresentation` / `AiAnalysis`：耗时来源改为用户语言；`historicalOverride`
  用于“版本/时间导致的历史结果”；AI 额外预留缺失时显示“未单独记录”。
- `TodayCommuteCard`：当前建议卡与历史卡分离；历史卡使用图标 + “历史建议，已过期/旧设置”
  + 灰色回看说明，点击只看详情；无当前建议时展示“当前没有适用的建议”。
- `CommuteDetailViews`：历史横幅（图标+文字）、处理状态横幅、设置更新横幅；
  智能分析卡改用 `summary` 作简短依据、缺失信息只显示数量口径；
  数据说明移除 provider 原始名、freshness 英文、reason code、模型/模板等内部字段。
- 演示构建新增 `COMMUTE_DEMO_CHECK=queued|failed`（仅 DEBUG/演示），用于验证等待/失败状态。

## 2. 真实验证（8010，expired 数据）

- 账号 `ui-a@example.com`；计划 revision 2；最新建议 `d845e2d4…`（10/24 21:54 出发，
  10/24 22:30 过期，quality=degraded，ai_analysis=available）。
- Today：主卡“当前没有适用的建议”，历史卡“历史建议，已过期 · 10月24日 21:40 生成”。
- 详情：顶部“历史建议，已过期”（图标+说明），AI 卡标“历史结果 / 智能分析可用（历史）”，
  耗时来源“路线快照时长”，AI 额外预留“10 分钟”，天气/路线获取时间保留。
- 杀进程恢复、Today/详情一致、A/B 隔离：UI 用例 **1 executed / 1 passed**（169.5s）。
- 只读：DB `commute_checks` 5→5、`commute_advisories` 5→5；没有新增 check/AI。
- 修复后单元测试：**115 executed / 115 passed / 0 failed / 0 skipped**。

## 3. Stub 状态验证（COMMUTE_DEMO，不代表真实后端）

- `COMMUTE_DEMO_CHECK=queued`：详情显示“分析请求已提交（时间），等待服务器处理”，
  按钮显示“已提交，等待服务器分析”，未出现百分比或计时动画。
- `COMMUTE_DEMO_CHECK=failed`：显示“上次分析失败”，天气卡标注“更新失败，当前显示上次数据”，
  分析按钮恢复可用；UI 用例 **1 executed / 1 passed**（43.0s）。
- 截图：`/tmp/demo_waiting.png`、`/tmp/demo_failed.png`。

## 4. 未验证 / 后端依赖

1. 真实后端目前只提供 queued/running 状态，没有更细阶段、进度百分比、开始时间或
   后台执行器可用性字段；客户端只展示真实状态，不补造阶段。若需要“处理到哪一步”，
   需要后端增加 stage / started_at / executor_status 等字段。
2. 只有“最新建议”接口；历史区只能展示一条最近历史，没有完整历史列表接口。
   未伪造列表。
3. 修改计划的界面全链路（改到达时间→保存→重新查询→新分析）本轮只有 Store 单测
   （旧建议转历史、旧 pending 清理、revision 隔离），未在模拟器点按完整表单；会创建新 check，
   按“不发起新模型运行”的原则未执行。
4. 等待/失败为演示 Stub；真实后端失败路径需后端可用后再验。
5. 未做真机验证。

## 5. 截图

- 历史建议 Today：`/tmp/state_today_final.png`
- 历史建议详情：`/tmp/state_detail_final.png`
- 等待/失败：`/tmp/demo_waiting.png`、`/tmp/demo_failed.png`
