# 后台运行状态读取（2026-10-25）

## 1. 接口与数据

- 新增 `GET /api/v1/commute/runtime`（统一 `PAConfig.baseURL` 与现有登录凭据），读取
  `server_time`、`scheduler/worker.state`、`worker.last_seen_at`、`stale_after_seconds`、
  `queue`、`latest_check`、`last_progress_at` 等字段。
- `latest_check` 只作参考，不覆盖当前页面的 `check_id`、`checkStatus` 或建议。
- 心跳是辅助信号：不改变任务状态、不重提、不取消服务器任务、不触发登出。

## 2. 展示规则

- 任务文字仍来自真实 check 状态/阶段：已提交等待、正在更新天气、正在查询路线、正在分析建议等。
- 辅助文字只有四种：
  - not_seen：尚未收到后台执行程序的响应。
  - stale：后台执行程序近期未响应，任务仍已保存。
  - online：后台执行程序最近有响应；并注明心跳正常不代表这条任务在推进。
  - 请求失败：暂时无法获取后台状态；任务仍已保存。
- 有上次结果时标明“这是上次查询的结果”；不会把缓存当成刚刚确认。
- 只在仍有排队/处理中/结果未知的任务时显示；建议完成后不占用主卡片。

## 3. 查询与时间

- 打开详情、任务开始/状态变化、回前台时读取；同一时刻共享一个请求，15 秒 TTL，打开页面不会重复发。
- 心跳年龄用 `server_time - last_seen_at` 计算，再加本地已流逝时间；设备时钟偏移不影响相对年龄，
  缓存的 online 超过 `stale_after_seconds` 会按 stale 显示。
- 退出/切号清空状态、取消任务并按 generation 丢弃迟到响应。

## 4. 验证

- 单元：`CommuteRouteTests` 覆盖 not_seen / stale / online / 请求失败 / 失败保留上次查询 /
  设备时间偏移 / 缓存变旧 / latest_check 不覆盖当前任务 / 切号迟到响应；全量单元 133 条通过。
- 模拟器（iPhone 18 Pro，演示仓库）：
  - 等待且执行程序未报到；
  - 近期未响应；
  - 心跳在线但任务仍排队；
  - 查询失败保留任务；
  - 重开页面恢复同一待处理任务（分析按钮保持禁用）。
  5 条 UI 用例通过；截图 `/tmp/rt_not_seen_final2.png`、`/tmp/rt_stale_final2.png`、`/tmp/rt_error_final2.png`。
- 隔离后端实际读取：`127.0.0.1:8410` 一次性 fixture（合成账号、mock provider、Nexus stub），
  `GET /api/v1/commute/runtime` 返回 `server_time`、worker `online`、scheduler `not_seen`、
  `stale_after_seconds=60`、`latest_check` 等；无令牌返回 401。验证后已停止并删除临时库。
- 共同后端 8010 未启动，未做真实账号联调；未新增模型调用/通知。

## 5. 未做

- 不把 heartbeat 当任务进度，不展示全量后台内部字段。
- 自动检查计划、执行器不可用专属字段等仍需后端另行提供。
