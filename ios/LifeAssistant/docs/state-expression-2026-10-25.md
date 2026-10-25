# 聊天核对 / 最新天气 / 真实进度接入（2026-10-25）

## 1. 连接方式

- 统一入口：`PAConfig.baseURL` 先读 `PA_API_BASE_URL`，再读 Info.plist 的
  `PAApiBaseURL`，最后回退 `http://127.0.0.1:8010`。
- `PAAPIClient`、`CommuteAPIClient`、会话续期都使用该入口；没有独立旧端口。
- 后端未运行时保留本机凭据与缓存，不自动切演示数据；演示只在 `PA_DEMO` / `COMMUTE_DEMO` 下启用。

## 2. 聊天历史

- `POST /api/v1/asks/verify`（每批最多 50 个 `local_run_ids`）核对本机缓存。
- `chat`：正常展示与恢复；服务端 `GET /asks` 返回的记录直接视为聊天。
- `not_chat`：从聊天时间线移除，不删除通勤建议/服务器记录，也不提供重新提问。
- `unknown`：保留本机数据，遮罩正文，只显示“这条历史记录暂时无法确认”，提供「稍后核对」；
  不直接重新提交。
- 没有服务器编号的未提交输入不参与核对，保留原文与幂等键，可继续恢复原提交。
- 核对失败（网络/5xx）保留原有展示；不会把“核对不了”当成 not_chat。
- 同一服务器编号的旧重复缓存会合并，分类应用到所有副本，避免漏出一条未遮罩正文。
- 账号切换清空核对状态；晚到的核对结果因账号校验不会写入新账号。

## 3. 天气与建议

- `GET /api/v1/commute-plans/{plan_id}/weather` 独立读取最新天气；打开详情只读刷新，不创建 check。
- 详情新增「最新天气」卡：温度、降雨概率（预报）、预报时段、获取时间、过期/旧计划提示；
  建议自己的「通勤时段天气」保持不变，并提示“天气已更新，下面的建议仍是上次分析的结果。”
- 建议卡拆分：固定预留、天气额外预留、总预留；模型额外预留只在 `ai_analysis` 有值时显示，
  缺失显示“未单独记录”。耗时来源只按服务器字段说明，不用数值推断“实时交通”。

## 4. 处理进度

- `CommuteCheck` 新增真实字段：`phase`（queued/fetching_route/fetching_weather/analyzing/saving/
  retry_wait/done/failed/cancelled）、`phase_updated_at`、`analysis_mode`、`next_attempt_at`。
- 有阶段显示阶段文字；没有阶段只显示“已提交/正在处理”，无百分比、无计时器切换。
- `retry_wait` 显示“请求已保存，后台处理暂不可用”；`status=unknown` 显示结果未知并继续核对。
- 轮询退避；进后台暂停本机轮询，回前台继续查询同一检查；重开 App 用持久化的 checkId
  继续查询，不创建新分析。

## 5. 验证结果（后端 8010 当前未运行）

- 单元测试：**123 executed / 123 passed / 0 failed / 0 skipped**。
- 模拟器 UI（iPhone 18 Pro，演示仓库）：
  - 旧缓存 unknown 遮罩正文并显示「稍后核对」：通过（8.8s）；
  - 最新天气卡与建议天气卡并存：通过；
  - 等待状态只显示已确认文字：通过（7.8s）。
- 截图：`/tmp/wt_chat_unknown2.png`、`/tmp/wt_weather_final.png`、`/tmp/wt_waiting_final.png`。
- 真实后端登录、真实核对、真实天气、真实检查关联：**未执行**（8010 无监听，按约定不自行启动）。

## 6. 仍需后端

- 真实阶段字段已按 OpenAPI 接入；若后端不返回 phase，客户端只显示状态。
- 后端错误码/执行器不可用标记需与真实环境确认；客户端不猜测。
- 旧缓存核对只按服务器分类展示，不按文本猜测。
