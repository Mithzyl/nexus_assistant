# 移动 Web 通勤分析推进记录（第三轮，2026-10-25 补记）

更新：2026-10-25。范围：`apps/web`、Web 测试、客户端文档。未修改 `services/assistant-api`、`ios/LifeAssistant` 或 `llm_fusion_platform`。

## 1. 本轮结论

- 通勤建议改为“只读查看已有建议”和“用户主动重新分析”两条路径；页面加载与刷新不创建 check。
- 重新分析不发送路线快照，由后端在有路线 Provider 时自行查询；Web 不生成 `mapkit_client` 或其他假快照。
- 当前 8010 联调环境没有可用的服务端地图 Provider：已有真实 AI 建议只能读取，不能称为“重新完成的服务端路线分析”。页面按实际情况显示“路线不可用 / 历史客户端路线快照 / 开发测试数据”。
- 修复了续期 Cookie 的时区问题：后端 `/auth/session/renew` 返回不带 `Z` 的 UTC 时间，旧实现会让浏览器 Cookie 提前 8 小时到期。现在统一规范化为 UTC 后再写入 Cookie。
- 增加同源写请求校验、账号缓存清理、保存地点复用与用户主动定位；未新增 Web Push、APNs、Bark 真实发送或模型运行。

## 2. 后端与数据来源

### 真实读取环境

- PA：`127.0.0.1:8010`，数据库 `/private/tmp/pa_notes.db`，只读使用。
- 账号：后端联调提供的合成契约 A/B（凭据不写入本文件、不进入仓库）。
- 计划：`契约测试通勤`（23:00 到达）；已有 advisory 为服务端保存的真实 Nexus 模型结果（`explanation_source=model`，`ai_analysis.status=available`）。
- 天气来源：Open-Meteo（服务端记录）。
- 路线来源：该条历史建议使用客户端路线快照（basis 中为 `provider=stub`）；当前 advisory 的路线来源显示为 `unavailable`。本轮没有重新创建 check，也没有调用模型。

### 隔离验证环境

- 独立 SQLite + 独立 uvicorn（`127.0.0.1:8013`），`ASSISTANT_COMMUTE_PROVIDER_MODE=mock`；仅用于验证 check 流程、幂等、关联关系与 UI 状态。
- 未启动 Nexus 模型；worker 处理检查时 AI 调用会失败并如实显示“模型分析未完成”，不会伪造成成功。
- 未开启真实 Bark、APNs、Web Push。

## 3. 页面与状态改动

### 3.1 只读与重新分析分离

- 打开/刷新 `/me/commute/[id]`：只 `GET plan`、`GET advisories/latest`，或恢复本机保存的 check 并 `GET checks/{id}`；不会 POST。
- 点击“重新分析”才创建 check；请求不带 `route_snapshot`、不带坐标、不带浏览器定位结果。
- 操作记录按账号和计划保存到 `localStorage`：幂等键、plan revision、check id、状态、关联 advisory id、错误。
- 提交超时/网络失败：保留同一幂等键，显示“提交结果不确定”；重试复用同一个键和同一请求内容。计划 revision 变化则作废旧状态。
- 刷新恢复：已存在 check 不会新建；终态按 `advisory_id` 读取 `/advisories/{id}`，不会拿另一条 latest 冒充本次结果。
- 无取消 check 接口：不伪造“已取消”；用户离开页面后服务器继续处理，重载可继续读取。

### 3.2 建议展示

新增展示：

- 路线来源：`server_provider` / `client_snapshot` / `none` / 未记录；
- 地图耗时、采用耗时、耗时来源（含交通/基础）；
- 计划缓冲、AI 额外预留；
- AI 状态（只有后端 `explanation_source=model` 且 `ai_analysis.status=available` 才显示“模型分析”）；
- AI 失败原因（中文映射）、依据 `basis`、缺失信息；
- 建议更新时间、有效期、过期标注；
- 任意 provider 为 mock/stub 时，模型标签降为“模型分析（基于开发/测试数据）”，并显示开发/测试数据警告。

### 3.3 路线未配置

- 后端 `ai_analysis.reason=route_provider_unconfigured` 或 check 错误码为 `provider_not_configured/eta_not_configured` 时，显示“路线服务尚未配置”，不生成 ETA、不显示假快照。
- 仅路线 source 为 `unavailable` 但没有明确 reason 时，显示“当前没有可用的服务端路线”；有历史客户端快照时明确说明“不是本轮 Web 发起的服务器路线查询”。
- 没有新的路线 Provider 时不新增地理编码/选点供应商；定位只产生浏览器坐标，不当作路线或交通事实。

### 3.4 保存地点与定位

- 新建/编辑计划时，从已有计划汇集去重的“已保存地点”，可一键填入起点或终点；没有地点搜索接口时明确提示。
- “使用当前位置”仅在用户点击时调用 `navigator.geolocation`；拒绝定位仍可使用已保存地点和手动坐标。
- 默认坐标为 0,0 并会被校验拒绝，避免硬编码或占位坐标被保存。

## 4. 会话与请求防护

Cookie 属性（生产 HTTP 测试值）：

```text
pa_session / pa_expires
HttpOnly=true
Secure=true（生产；dev 为 false）
SameSite=Lax
Path=/
Max-Age 与服务器 expires_at 对齐
```

- 续期：`/auth/session/renew` 返回值缺少时区时补 `Z` 后重新计算 Cookie；实测浏览器 Cookie 到期时间与服务器保存的截止时间一致，不再提前 8 小时。
- 当前 8010 的续期返回同一绝对期限（未延长）；Web 只是同步该期限，不虚构延长。
- 同源写请求：POST/PUT/PATCH/DELETE 校验 `Origin` host 与 `Sec-Fetch-Site`。跨站写实测返回 403，且不撤销会话。
- 普通资源 401 仍按原策略：仅明确的会话失效清理；403 不触发全局登出。
- 有效会话过期：本机记忆最近账号；`/auth/me`/renew 401 时删除该账号的问答缓存、待处理 check 缓存和最后账号标记；其他账号与访客缓存不动。本地“收下内容/记一句”作为用户数据保留。
- 生产环境使用 Secure Cookie；本地 `next start` 走 HTTP 时，WebKit 不发送 Secure Cookie（Chromium 对 localhost 有例外）。因此 WebKit 登录验证使用 dev server（Secure=false），真实部署必须 HTTPS。

## 5. 验证记录

| 检查 | 环境 | 结果 |
|---|---|---|
| 类型检查 | `tsc -p apps/web/tsconfig.json --noEmit` | 通过 |
| 生产构建 | `pnpm build:web` | 通过 |
| 真实 AI 建议只读 | live 8010，契约 A | 读取到已有模型建议；打开/刷新无 POST；刷新恢复 |
| 账号切换 | live 8010，契约 A/B | B 看不到 A 的计划；重登 A 建议恢复 |
| 显式重新分析 | 隔离 mock 8013 | 创建 1 个 check；刷新恢复同一 check；不重复创建 |
| 关联建议 | 隔离 mock | 终态按 `/advisories/{id}` 读取，重载没有请求 latest |
| 幂等重试 | 隔离 mock + 拦截 502 | 同键重试成功，仅创建 1 个 check |
| 路线来源/耗时 | 隔离 mock | 显示“服务器路线查询”、含交通耗时、计划缓冲；Mock 警告 |
| AI 失败 | 隔离 mock | 显示“模型分析未完成 / AI 服务调用异常”，未标成成功 |
| 路线未配置 | 隔离库模拟契约状态 | 显示“路线服务尚未配置”，不生成新 ETA |
| 过期建议 | 隔离库 | 显示“这条建议已过期” |
| Cookie 属性 | 生产 Chromium | HttpOnly / Secure / SameSite=Lax / Path=/ / 到期与值一致 |
| Cookie 续期 | 生产 Chromium | 续期后 Cookie 到期时间不缩短，并带时区 |
| 跨站写防护 | 真实 HTTP | evil Origin 403；同源 renew 200；会话保留 |
| 会话过期清理 | 隔离 Chromium | 无效 Cookie → 未登录、Cookie 删除、该账号缓存清理，其他账号保留 |
| 保存地点 | Chromium | 已有计划地点可选入起点/终点 |
| 当前定位 | Chromium（授权/拒绝） | 仅点击后请求；成功填入坐标；拒绝有提示且保存地点可用 |
| 响应式/深色 | Chromium 390/360/430 | 无横向溢出；底部导航不遮挡；深色截图 |
| WebKit | Playwright WebKit + dev server | 登录、通勤详情、建议渲染通过；无 pageerror；不等同真实 Safari |

截图位于 [screenshots/mobile-web/](screenshots/mobile-web/)：

- `01-real-ai-advice.png`：live 8010 读取已有真实 AI 建议（路线为历史客户端快照，已明确标注）。
- `02-server-route-mock-analysis.png`：隔离环境服务端路线查询 + Mock + AI 失败。
- `03-route-unconfigured-expired.png`：模拟“路线服务尚未配置”和过期标注。
- `04-saved-places.png`：复用已保存地点。
- `05-session-expired.png`：会话失效清理后的未登录状态。
- `06-webkit-commute.png`：Playwright WebKit 通勤页。
- `07-dark-mode.png`：深色模式。

## 6. 未完成与边界

1. **真实地图 Provider 未就绪**：8010 当前路线来源为 unavailable；没有做真实 Maps/路线查询的最小端到端。UI 已支持 `server_provider` 展示，但未用真实供应商验证。
2. **未新增模型运行**：只读取已有真实 AI 建议；AI 成功路径的新分析依赖真实 Nexus，本轮未执行。
3. **无 check 取消接口**：Web 不伪造取消；页面关闭后由服务器继续。
4. **无地点搜索/地址解析接口**：仅使用已有计划地点和浏览器定位坐标。
5. **Bark/APNs/Web Push**：本轮未发送真实提醒，未新增 Web Push。
6. **WebKit 不等于 Safari**：未做真实 iPhone Safari 键盘、底部安全区、Cookie 真机验证。
7. **后端续期行为**：当前测试环境 renew 返回同一期限；如果后端改为延长，Web 已按返回期限同步 Cookie。
8. 2026-10-25 后端已提供 `GET /commute/runtime`（含 worker/scheduler 心跳与 route_provider_available）和 `GET /commute-plans/{id}/weather`；Web 已接入。真实地图 Provider 仍未配置，隔离测试使用 mock。
9. 未修改后端、iOS 或 AI 平台；未提交、推送或部署。

## 7. 运行

```bash
cd /Users/mith/Desktop/project/superapp-next
PA_API_URL=http://127.0.0.1:8010 pnpm dev:web
# http://localhost:3000/me/commute
```

生产构建：`pnpm build:web`，随后 `pnpm --filter @superapp/web start`。生产环境应使用 HTTPS，以保证 Secure Cookie 可用。


## 8. 2026-10-25 补记（本轮 Web 改动）

- 聊天历史与通勤内部分析分开：旧本机缓存通过 `POST /asks/verify` 批量核对；`not_chat` 从聊天和重新提问入口移除，`unknown` 保留本机记录但隐藏正文并禁止直接重提。
- 详情页新增独立“最新天气”区域，读取 `/commute-plans/{id}/weather`；建议卡改为“上次分析采用的天气”。两者时间不一致时显示“天气已更新，出发建议仍来自上一次分析”；缺失天气不再借用其他日期/地点。
- 服务端新增的 `matches_current_plan` / `is_current_service_date` / `is_expired` 优先用于适用性判断；旧缓存缺少这些字段时回退到 plan revision 与有效期。计划修改显示“修改前结果”，跨日记录显示“历史建议”。
- check 详情新增 `phase`、`analysis_mode`、`phase_updated_at`；页面显示真实后台步骤（更新天气、查询路线、分析建议、保存结果等）与后台心跳。后台无响应时说明任务已保存，不再无限显示分析中的旋转图标；结果未知单独提示。
- 只读恢复、单次重新分析、刷新恢复同一 check、按 `advisory_id` 读取本次建议、计划版本冲突、路线未配置和过期历史均有隔离环境浏览器覆盖。
- 可重复测试：`ASSISTANT_PYTHON=... pnpm --filter @superapp/web test:e2e`（独立 SQLite、固定时钟、3300/8300 端口、合成账号，详见 `tests/e2e/README.md`）。最近一次主流程 **90/90**、后台状态 **23/23** 通过，0 pageerror。
- 边界未变：共享 8010 后端本轮未启动、未连接；真实 Nexus、真实地图 Provider、Bark 送达、APNs/Web Push、HTTPS/Safari 实机仍未验证。测试中的 mock 天气/路线不是真实路况或预报。


## 9. 2026-10-25 后台状态语义修正

- `worker.state=online` 只表示服务器最近收到过心跳，不等于进程此刻持续在线。页面用 `server_time` 加本地单调时钟换算 `last_seen_at` 的年龄；超过 `stale_after_seconds` 后即使响应里仍是 `online`，也显示“近期未响应”。
- 状态文案：`online` → “最近有响应”（带约 X 秒/分钟前）；`stale` → “近期未响应（上次响应…）”；`not_seen` → “尚未收到执行程序的响应”；请求失败 → “暂时无法获取后台状态”，并显示上次成功查询时间。以上都不把任务判为失败。
- 三个信号保持分离：`route_provider_available` 只回答路线是否可用；`check.status/phase` 只回答当前任务做什么；`worker.last_seen_at` 只回答后台最近是否报到。`latest_check` 不替换当前打开的 check。
- 被查看的任务仍在等待/处理时，后台状态只作为辅助说明；任务完成后不再保留醒目的后台横幅。页面不因状态变化自动重新分析、取消或创建检查。
- 活动任务时每 20 秒刷新一次；失败后按 20→40→60 秒退避；页面隐藏时停止轮询，重新可见时立即核对。旧请求用序号和 AbortController 丢弃，账号/计划切换会重置状态。
- 浏览器测试使用受控 `/commute/runtime` 响应覆盖上述场景，同时保留隔离真实后端读取计划/检查；主流程 90/90 + 后台状态 23/23 通过。
