# 后台提示随时间变化与应用真实读取（2026-10-25 复核）

## 1. 复核发现并修复

- 页面停更新：原实现只在状态变化时重算，`runtimeHint` 不会随时间自动刷新。
  现在详情用 `TimelineView(.periodic(by: 5))` 重算文字，不额外发网络请求。
- 手机时间偏差：原先用墙钟 `Date() - fetchedAt` 计算快照已过的时长。现在快照读取时刻
  记录在单调时钟（`systemUptime`）上，心跳年龄 = 服务器 `server_time - last_seen_at` +
  单调时钟已过时长；用户改手机时间不会改变判断。
- 计划切换：保存新 revision 时清除旧 `checkStatus/checkPhase/runtime`，旧任务的等待/后台提示
  不会出现在新计划上；删除计划同样清理。
- 保留原有 not_seen / stale / online / 请求失败表达与切号 generation 守卫。

## 2. 模拟器验证（演示仓库，受控时间）

- 新增 `testOnlineHintAgesToStaleWhilePageStaysOpen`：online、seenAgo=1s、阈值=3s，
  页面停留后自动从“最近有响应”变为“近期未响应，任务仍已保存”，任务仍等待。
- 新增 `testReturningToDetailReadsUpdatedRuntime`：离开详情再回来会重新读取，显示更新后的状态。
- 原有 5 条 runtime UI 用例继续通过；共 **7 条 UI 用例通过**。
- 单元测试新增“单调时钟年龄”和“计划切换清理”，全量 **135 条通过 / 0 失败**。
- 截图：`/tmp/runtime_aged_stale.png`（停留后变近期未响应）、
  `/tmp/rt_not_seen_final2.png`、`/tmp/rt_error_final2.png`。

## 3. 应用真正连接后端（本轮新增证据）

上一轮的 8410 读取是命令行 curl，不是 App 发起。本轮新增 `LiveRuntimeUITests`，
在 iPhone 18 Pro 模拟器上用 `PA_API_BASE_URL=http://127.0.0.1:8410` 启动正式构建（未启用 PA_DEMO）：

- `testRuntimeNotSeenAgainstIsolatedBackend`：App 登录合成账号 runtime-a、打开服务器计划、
  点“重新分析”创建 check；页面显示“尚未收到后台执行程序的响应。”。通过。
- `testRuntimeOnlineAgainstIsolatedBackend`：隔离后端 worker 心跳后，App 再次发起分析，
  页面显示“后台执行程序最近有响应。”。通过。
- 两个用例均在 App 内完成登录与页面操作；后端为一次性隔离实例（临时 SQLite、mock provider），
  未连接 8010，未调用真实模型；验证后已停止并清理。
- 截图：`/tmp/live_not_seen.png`（真实服务器返回 not_seen，任务仍在等待）。

## 4. 仍然保持

- 心跳读取失败不登出、不判任务失败、不新建检查；保留上次结果并标注“上次查询”。
- 任务完成后后台提示自动隐藏；切号迟到结果丢弃；latest_check 不覆盖当前 check。
- 打开、刷新、重启不会创建新检查（重开恢复同一待处理任务用例继续通过）。
