# 移动 Web 实现与验证

> **本文是实现与验证记录（截至 2026-10-25），不是产品规划，也不是最新验收结论。**
> 当前规划见 [`产品规划-工作安排与计划接续.md`](产品规划-工作安排与计划接续.md)；逐项差距见 [`最新规划落地对照-现有能力与差距.md`](最新规划落地对照-现有能力与差距.md)。文中的 90/90、构建通过等结果只覆盖当时列出的行为，不覆盖现行规划的事项、确认写入、待同步状态与冲突检查。

更新时间：2026-10-25。本文记录移动 Web 的实际实现、真实接入、演示范围、验证证据与剩余依赖；2026-10-25 新增聊天缓存核对、最新天气、后台步骤与可重复浏览器测试。

## 1. 结论

- 页面与交互：四个主入口（今天、事务、助手、我的）、详情页、底部导航、录入弹层、深色模式与手机断点已实现。
- 真实接入：PA 登录/续期/退出、问答提交与查询、SSE 事件与刷新恢复、内容来源与云端内容、笔记导入、通勤计划/检查/建议、Bark 绑定/开关/解除。
- 聊天历史以服务器聊天接口为准；旧版本地缓存用 `POST /asks/verify` 批量核对。内部通勤分析记录不再进入聊天，已确认 `not_chat` 的记录从展示与重新提问入口移除，`unknown` 保留本机记录但隐藏正文并禁止直接重提。
- 最新天气使用独立的 `GET /commute-plans/{plan_id}/weather`，与建议生成时保存的天气分开展示；“天气已更新”不等于建议已重新分析。
- 通勤详情展示服务器 check 的真实阶段（更新天气、查询路线、分析建议、保存结果等）与后台服务心跳；`online` 只表示“最近有响应”，页面会按 `server_time` 与本地经过时间把旧心跳降级为“近期未响应”。状态请求失败时显示“暂时无法获取后台状态”和上次成功查询时间，不把网络失败误写成执行程序离线。后台无响应时说明任务已保存，不会用无限旋转图标冒充分析中。
- 本地或演示：本机“收下内容/记一句”记录、事务详情（退款/订单/票据/心愿/项目/行程）、个人记忆与经验。均需显式开启演示模式，页面有明确标注。
- 原生限制：浏览器没有 EventKit、MapKit、APNs、系统分享和 Apple 备忘录读取；没有用虚构坐标、路线快照或送达回执替代。
- AI 平台目录未做任何写入。本任务没有启动、修改或迁移 `/Users/mith/Desktop/project/llm_fusion_platform/`。

## 2. 功能对应表

| iOS 页面或操作 | 当前数据来源 | Web 页面与交互 | 已有接口 | 本次实现方式 | 验证方式 |
|---|---|---|---|---|---|
| 今天 | 本地日志 + 通勤 API | `/today`：日期、最近整理、通勤卡、待确认（演示） | `GET /commute-plans`、`GET /advisories/latest` | 真实读取；无建议/未登录/失败分状态；本机记录只在浏览器 | Chromium 实测登录/未登录 |
| 收下内容、记一句 | 本地 | 全局底部弹层 | 无 | 真实模式只存本机并明确说明；演示模式复刻示例结果矩阵 | 浏览器实测保存/删除 |
| 事务列表与筛选 | 本地演示 | `/matters` 进行中/已完成/全部 | 无 | 正常模式空状态 + 能力说明；演示模式显示示例 | 浏览器实测筛选/卡片 |
| 退款、收纳架 | 本地演示 | `/matters/coffee`、`/matters/shelf` | 无 | 退款阶段、回执到账、撤销、重新应用；金额相同不合并 | 浏览器实测到账/撤销 |
| 归属确认 | 本地演示 | `/matters/ambiguity` | 无 | 只关联、不标记到账 | 浏览器实测确认 |
| 出门准备、票据 | 本地演示 | `/matters/prepare`、`/matters/movie` | 无 | 清单勾选、记忆停用后移除条目、票据信息 | 浏览器实测勾选/记忆开关 |
| 心愿、项目、行程、订单、待整理 | 本地演示 | `/matters/wish`、`/matters/project`、`/matters/travel`、`/matters/lamp`、动态 `/matters/[id]` | 无 | 安排确认/取消、步骤推进、改期冲突与待联系清单 | 浏览器实测 |
| 原始依据 | 本地演示 | `/matters/evidence/[id]` | 无 | 等宽原文与来源范围说明 | 浏览器实测 |
| 助手问答 | PA API | `/ask` | `POST /asks`、`GET /asks`、`GET /asks/{id}`、`GET /asks/{id}/events`、`POST /asks/{id}/cancel`、`POST /asks/verify` | httpOnly Cookie + 同源代理；SSE 读取，终态后以详情接口的 `answer` 为准；失败回退有界轮询；稳定幂等键；旧缓存按 50 个一批核对，内部记录移除、未知记录隐藏且不可重提 | 隔离后端 + Playwright 实测登录/历史核对/刷新/账号切换 |
| 登录、续期、退出、过期 | PA API | `/login`、`/me` | `POST /auth/login`、`GET /auth/me`、`POST /auth/session/renew`、`POST /auth/logout` | 服务端转发，令牌只存 httpOnly Cookie；续期沿用固定 Bearer；退出撤销当前设备 | 浏览器实测错误密码/登录/续期/退出 |
| 内容来源 | PA API | `/me/sources` | `GET/POST/PATCH/DELETE /data-sources`、`DELETE /items` | 列表、开关、字段授权、删除副本/来源语义 | 浏览器实测来源列表/开关/字段/删除 |
| 云端内容 | PA API | `/me/sources` 云端内容、`/me/sources/content/[id]` | `GET /content`、`GET /content/{id}` | 搜索、类型筛选、详情；字段为空不推断为删除 | 浏览器实测导入后搜索/详情 |
| 笔记导入 | 浏览器文件 + PA API | `/me/sources/import` | `POST /data-sources`、`POST /data-sources/{id}/sync` | 仅纯文本/Markdown；确认后上传；稳定内容哈希去重；显示新建/更新/未改动 | 浏览器实测落库与搜索 |
| 通勤设置 | PA API | `/me/commute`、`/new`、`/[id]` | `GET/POST/PUT/DELETE /commute-plans` | 手动坐标、完整表单校验、If-Match 冲突提示；不生成路线快照 | 浏览器实测创建/编辑/删除 |
| 手动检查、建议 | PA API + worker | `/me/commute/[id]` | `POST /commute-plans/{id}/checks`、`GET /checks/{id}`、`GET /advisories/latest`、`GET /advisories/{id}`、`GET /commute-plans/{id}/weather`、`GET /commute/runtime`、`POST /advisories/{id}/actions` | 幂等键、真实阶段与后台心跳、最新天气与建议天气分开、按 advisory_id 读取本次结果、服务器适用性标注、跳过今天 | 隔离后端 + 固定时钟实测只读恢复/单次创建/关联读取/过期与旧版本 |
| Bark | PA API | `/me/commute/bark` | `GET/PATCH /commute/bark`、`POST/DELETE /bark/binding`、`POST /bark/test` | 绑定/替换/解绑、自动提醒开关、显式测试；区分“接受”与“送达” | 浏览器实测绑定/开关/解绑（未真实发送） |
| 个人记忆、经验 | 本地演示 | `/me/memories`、`/me/lesson` | 无 | 显式演示；可编辑/停用；正常模式说明无写入接口 | 浏览器实测 |
| 记忆检索 | PA 遗留代理 | `/me/memory-search` | `POST /memory/search` | 只读，错误不显示为空结果；不新增管理能力 | 契约桩实测返回 2 条 |
| 隐私、关于 | 静态事实 | `/me/privacy`、`/me/about` | 无 | 按实际保存/删除/限制描述，不承诺未实现能力 | 代码与页面核对 |

## 3. 真实、演示与缺失

**真实接入**

- 账号与会话：登录、身份查询、续期、退出；Cookie 为 httpOnly，令牌不进入 JavaScript。
- 问答：提交、状态、SSE、取消、刷新恢复、历史合并、失败与未知状态。
- 内容来源：列表/详情/同步开关/助手授权/字段授权/删除副本/删除来源；云端内容搜索与详情；浏览器文本笔记导入。
- 通勤：计划 CRUD、手动检查与轮询、最新建议、打开/跳过动作、Bark 绑定与开关；后台状态只表示最近一次服务器响应，不承诺进程持续在线，也不与任务阶段或路线可用性互相替代。
- 记忆检索：遗留只读代理，失败时明确显示不可用。

**本地或演示**

- “收下内容/记一句”在正常模式只写浏览器 localStorage（按账号隔离），结果明确说明未上传。
- 事务/退款/订单/票据/心愿/项目/行程/归属确认/出门清单/个人记忆属于本地演示，需在“我的”显式开启；刷新恢复初始示例。
- 演示模式不会在网络失败时自动启用；真实请求失败只显示真实失败/空状态。

**后端没有的能力（未虚构接口）**

- 事务、退款、订单、票据、心愿、项目、行程的服务器接口。
- 个人记忆 CRUD、自动学习、偏好同步。
- 会话上下文/连续对话、历史分页、记忆列表接口。
- 通知历史、Bark 送达回执。
- 注册、找回密码、第三方登录。

## 4. 验证记录

### 4.1 2026-10-25 可重复验收（本轮）

`tests/e2e/run_web_e2e.py` 会新建独立 SQLite、写入合成账号/计划/聊天/天气，以固定时钟启动真实 `assistant-api`，再启动生产构建并用 Chromium 执行 `web_flow_check.py`。共享后端、真实模型和通知均不参与。

```bash
python3 -m pip install playwright && python3 -m playwright install chromium
python3 -m venv .tmp/backend-venv
.tmp/backend-venv/bin/pip install -e services/assistant-api
ASSISTANT_PYTHON=.tmp/backend-venv/bin/python pnpm --filter @superapp/web test:e2e
```

默认端口：网页 3300，隔离 API 8300；测试账号仅存在于临时库，见 `tests/e2e/README.md`。最近一次结果：主流程 **90/90**、后台状态 **23/23**、真实事项/草稿 **38/38** 通过，0 pageerror；`tsc --noEmit` 与 `next build` 均通过；截图在 `.tmp/e2e/shots/`。覆盖登录/续期/退出/账号切换、跨站写拒绝、旧缓存三类核对、离线保留、最新天气与旧建议分离、只读打开不建任务、单次重新分析与刷新恢复、按 advisory_id 读取、结果未知、路线未配置、计划修改、过期历史、360px 长文本与深色模式。后台状态专项还覆盖：执行程序从未报到、刚报到但任务未领取、心跳超阈值、查询失败、长时间打开后老化、页面隐藏/恢复、账号切换不串状态，以及这些读取不创建检查。真实事项/草稿覆盖四入口、事项三组、详情四块、真实标题纠正/草稿进展/待办勾选、冲突方案、停止/删除/重开、实验室隔离、PWA 清单与图标，以及不出现虚假 iPhone 写入承诺。

### 4.2 额外模型流程（2026-10-25，未稳定通过）

`tests/e2e/acceptance_model_flows.py` 的两条模拟模型流程本轮重跑**间歇失败**：网页点击“重新分析”后，创建 check 时后端返回 500，数据库中计划存在但没有 instance/check。错误栈为 `ensure_instance()` 的 `db.begin_nested()` → `sqlite3.OperationalError: no such savepoint: sa_savepoint_1`；同一命令、同一提交下跨 SQLAlchemy 2.0.51 与 2.1.6 均出现，2 次通过、3 次失败。不能把单次 23/23 当作稳定通过。

测试侧已修正实际导入路径（强制 worktree 的 `services/assistant-api/src` 并打印校验），失败证据、完整命令、版本、数据库状态和原始日志在 `tests/e2e/evidence/model-flow-failure-20261025/`。等后端给出 savepoint/单连接池并发问题的修复提交与稳定地址后再重跑；主流程 90/90 与后台状态 23/23 不受影响。

### 4.3 早期验证（2026-10-24，保留作历史记录）

环境：macOS；Next.js 14；Node 22；Chromium（Playwright）；PA API 与 nexus-stub 通过真实 HTTP 启动；通勤 mock provider + worker。

| 检查 | 命令/方式 | 结果 |
|---|---|---|
| 类型检查 | `apps/web/node_modules/.bin/tsc -p apps/web/tsconfig.json --noEmit` | 通过 |
| 生产构建 | `pnpm build:web` | 通过（移动页面、API 转发与旧看板均保留） |
| 手机断点 | Chromium 390×844、360×844、430×844 | 无横向溢出，底部导航不遮挡正文 |
| 深色模式 | 手动切换“深色/浅色/跟随系统” | 颜色变量与卡片/输入/导航均生效 |
| 未登录 | `/today`、`/matters`、`/ask` | 空状态与登录提示，不显示示例冒充真实 |
| 登录失败/成功 | 错误密码、正确密码 | 401 显示“邮箱或密码不正确”；成功后进入今天 |
| 续期/退出 | “立即续期”“退出登录” | 服务器确认新期限；退出撤销并回游客态 |
| 问答 | 真实 HTTP + 契约桩 | 202→SSE→终态；答案取自 `GET /asks/{id}`；刷新后仅一条；失败/取消有状态 |
| 本地收录 | 粘贴→保存→今天列表→删除 | 保存与删除生效，明确未上传 |
| 事务演示 | 到账回执、撤销、归属确认 | 商家已发起≠到账；撤销可恢复；关联不标记到账 |
| 笔记导入 | 新建标题/正文→确认 | 服务器返回新建 1；云端内容可搜索、详情可读 |
| 来源语义 | 同步/助手开关、字段授权、删除入口 | PATCH 生效；删除副本与删除来源分开确认 |
| 通勤 | 新建计划→手动检查→建议 | 真实 worker 生成 mock 建议；时间、天气、来源、过期均来自响应 |
| Bark | 绑定→自动开关→解绑 | 状态正确；未执行外部测试发送 |
| 续期 | “立即续期” | 服务器确认新期限 |
| 账号切换 | A→B→A | 问答历史与本机记录互不残留、重新登录恢复 |
| Markdown 表格 | 模拟回答内容渲染 | 表格内部滚动，页面无横向溢出 |
| 控制台 | 生产构建页面 | 无 pageerror；仅有按设计返回 404（无建议）与登录错误探测 |

修复后的最终生产构建回归（`next build` + `next start`）：

```text
PASS ask answer updates in place
PASS ask has one block per submission
PASS demo source preview link opens evidence
PASS source upload field toggles via API
PASS empty coordinates rejected
PASS commute saves with manual coordinates
PASS commute check gets server advice
PASS bottom nav does not cover footer
PASS ask no horizontal overflow at 360
9/9 passed, 0 pageerror
```

账号与会话补充检查：

- 续期：点击“立即续期”，服务器返回新的期限，页面显示“已向服务器确认续期”。
- 账号隔离：账号 A 提问并保存本机记录 → 退出 → 登录账号 B：B 的问答历史与本机记录都不含 A 的数据；再登录 A，原问答与本机记录恢复。
- 返回：来源详情和事务详情的圆形返回按钮都回到上一级，不会反复跳转。
- 长文本：360px 宽下打开超长本机记录，页面无横向溢出。

Markdown 表格采用模拟结果验证：在隔离测试库中把一条已完成回答替换为包含二级标题、表格、加粗、行内代码、列表和引用的 Markdown，再经真实 `GET /asks/{id}` 渲染。`.md-table-wrap` 内部可横向滚动（client 310 / scroll 420），页面本身无横向溢出，360px 下同样成立。

首轮自动化 25 项里有两项失败：一项是检查脚本错误断言（登录后“今天”页本来就不展示用户名），另一项是真实缺陷——SSE 终态刷新使用提交前的旧闭包导致答案不显示。已改为读取 `itemsRef` 并在终态后调用详情接口，复测通过。

未验证：真实 NexusPilot 模型调用（本轮未启动共享后端或平台）、Bark 真实送达、APNs、真实 iPhone Safari 键盘/安全区。2026-10-25 的浏览器验收使用隔离 mock 路线/天气，不声称真实路况或真实预报。

## 5. 启动方式

### PA 后端（单独终端）

```bash
cd services/assistant-api
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # 修改两个令牌；本地可用 mock 通勤
ASSISTANT_ALLOW_SCHEMA_CREATE=true .venv/bin/python -m uvicorn assistant_api.main:app --port 8010
```

如需扫描认证和真实问答，需要 NexusPilot 在配置的地址运行；通勤检查需要独立 worker：

```bash
.venv/bin/python -m assistant_api.commute.worker
```

本地账号使用 `assistant_api.commands.seed_dev_user` 创建，密码通过 `ASSISTANT_PROVISION_PASSWORD` 提供，不写入命令历史。

### 移动 Web

```bash
pnpm install
PA_API_URL=http://127.0.0.1:8010 pnpm dev:web
```

打开 `http://localhost:3000/today`。`/` 会重定向到 `/today`；旧看板保留在 `/board`，Electron 入口已指向该路由。

生产构建：

```bash
pnpm build:web
pnpm --filter @superapp/web start
```

`PA_API_URL` 缺省为 `http://127.0.0.1:8010`。浏览器只访问同源 `/api/auth/*` 与 `/api/pa/*`。

可重复验收（不连接共享后端）：

```bash
ASSISTANT_PYTHON=.tmp/backend-venv/bin/python pnpm --filter @superapp/web test:e2e
# 或复用已构建产物：python3 tests/e2e/run_web_e2e.py --skip-build
```

## 6. 剩余事项（任务清单已删除）

> 2026-10-26 清理：本节原有 6 条“剩余事项”是 2026-10-25 的待办清单，已过时，任务项已删除。当时未实现、未验证的范围已在本文第 3、4 节保留；当前任务与进度见 [`开发安排.md`](开发安排.md) 与 [`最新规划落地对照-现有能力与差距.md`](最新规划落地对照-现有能力与差距.md)。

## 7. AI 平台只读声明

本任务对 `/Users/mith/Desktop/project/llm_fusion_platform/` 全程只读：未新增、修改、删除、移动或重命名任何文件；未在该目录运行会产生构建/缓存/依赖安装结果的命令；未启动、重启或迁移该平台。移动 Web 的 AI 调用仍走 PA `assistant-api`，浏览器不接触平台令牌或角色绑定。
