# SuperApp

## 当前阶段（2026-11-09 更新）

- **现行规划**：[三阶段八项能力](docs/产品规划-工作安排与计划接续.md)，旧用户原文和历史剧本保留在后部。
- **交付重点**：统一真实输入、按任务采用记忆、今天页下一步；后续持续关注、自动安排、简报复盘和模板。
- **当前事实**：已有真实事项/日程/待办/提醒、结果卡片、补充与跟进，以及Assistant自有记忆。全局输入仍本机暂存，下一步未实施，完整阶段验收未完成；不能照旧main清单重建已有能力。
- **执行与状态**：[Web开发安排](docs/开发安排.md)、[落地对照](docs/最新规划落地对照-现有能力与差距.md)、[文档目录](docs/文档目录.md)。后端定义以实际backend/dev为准；本Web分支旧后端副本不代表本机运行版本。

> 以下“看板 + LLM 聊天”内容是该仓库早期阶段的实现，保留作历史说明，不代表当前产品范围。

**早期阶段简介（历史）**：个人超级应用：看板（Kanban）+ LLM 聊天（带 function calling）。

**LLM 底层复用 [NexusPilot](../llm_fusion_platform/) LLM 平台**（本地部署，HTTP API 接入）。模型调用、会话/消息存储、运行与任务审计、费用与调用证据由平台承担，本仓库不重复自研 LLM 基础设施。早期阶段本仓库专注看板领域数据、界面与工具执行；当前阶段的移动 Web 与 PA 后端见下文。

## 移动 Web（生活助手）

`apps/web` 现在同时承载 Personal Assistant 移动 Web 客户端和旧版看板：

- 移动入口：`/` → `/today`。当前底栏为“今天 / 事项 / 问助手 / 我的”。页面复用 `services/assistant-api`，浏览器只访问同源转发层。
- 旧版看板：保留在 `/board`；Electron 开发入口已指向该路由。
- 会话令牌保存在 httpOnly Cookie，不进入浏览器 JavaScript；AI 平台令牌与模型绑定只在服务端。
- 消费/退款等示例保持演示隔离；正式事项页面已接真实后端，不能将其与演示数据混为一谈。
- 现行规划要求的待确认变更卡、确认后写入日程/提醒、iOS 待写入队列、前后 3 小时冲突检查仍未实现；逐项差异见 [`docs/最新规划落地对照-现有能力与差距.md`](docs/最新规划落地对照-现有能力与差距.md)。
- 启动、接口对应表、验证结果与剩余依赖见 [`docs/移动Web实现与验证.md`](docs/移动Web实现与验证.md)。
- 通勤分析、路线来源、会话 Cookie 与本轮验证见 [`docs/移动Web通勤分析推进记录.md`](docs/移动Web通勤分析推进记录.md)。
- 可重复浏览器验收：`ASSISTANT_PYTHON=<隔离后端解释器> pnpm --filter @superapp/web test:e2e`。它使用临时 SQLite、固定时钟和合成账号，不连接共享后端；命令细节见 [`tests/e2e/README.md`](tests/e2e/README.md)。该验收覆盖的是既有行为，不包含现行规划的事项与确认写入流程。

```bash
# 单独启动 PA API 后（默认 http://127.0.0.1:8010）
pnpm dev:web
# 打开 http://localhost:3000/today
```

如果 PA API 不在默认端口，用服务端环境变量指定，例如 `PA_API_URL=http://127.0.0.1:8013 pnpm dev:web`。

## 结构

pnpm monorepo：

```text
apps/
├── web/          # Next.js 14 (App Router) + React 18 + Tailwind (shadcn/ui)
│   ├── app/      # 页面（移动端 today/matters/ask/me；旧看板 /board）
│   ├── components/mobile/  # 移动端页面与弹层组件
│   ├── components/board/   # 旧看板组件
│   ├── lib/db/   # 数据层：electron.ts | web.ts | index.ts 环境检测路由
│   └── prisma/   # Prisma + SQLite（看板领域数据）
├── desktop/      # Electron 31 壳，主进程持有 SQLite（Prisma），经 IPC 暴露
└── shared 包: packages/shared  # 跨端共享类型（Board/Column/Card）

services/
└── assistant-api/  # Personal Assistant 后端（认证、问答、来源、通勤等）
```

- `apps/web` 在浏览器中运行：看板数据走本地存储 stub（Phase 2 规划 IndexedDB）
- `apps/web` 嵌入 Electron 运行时：看板数据走 IPC → 主进程 Prisma → SQLite
- LLM 功能统一走 NexusPilot HTTP API（经 Next.js API Route 代理，见下）

## 早期看板架构（历史）

```text
┌──────────────────────┐          ┌──────────────────────┐
│   Web (浏览器)        │          │   Desktop (Electron) │
│   Next.js 14          │          │                      │
│                      │          │                      │
│  看板 UI              │          │  看板 UI (同一 Next)  │
│      │               │          │      │               │
│      ▼               │          │      ▼               │
│  lib/db (IndexedDB)  │          │  IPC → Prisma/SQLite │
│      │               │          │      │               │
│      ▼               │          │      ▼               │
│  API Route 代理 (LLM)│◄─────────┼──────┘               │
└──────┬───────────────┘          └──────────────────────┘
       │ HTTPS + X-API-Key
       ▼
┌──────────────────────────────────────────────────────────┐
│  NexusPilot LLM 平台（本地部署，见 llm_fusion_platform）  │
│  POST /api/v1/responses（普通 + SSE）                     │
│  Session / Message / Run / Task 管理与审计                │
│  多供应商 Model Gateway（OpenAI/DeepSeek/Anthropic/…）   │
└──────────────────────────────────────────────────────────┘
```

**边界**：

| 归属 | 能力 |
|---|---|
| NexusPilot 平台 | 模型调用（普通/SSE）、会话与消息存储、Run/Task 审计、费用与调用证据、多供应商路由 |
| superapp | 看板领域数据（Board/Column/Card）、界面、工具执行（function calling 中作用于看板的工具） |

API Key 不进入浏览器端：所有 LLM 请求经 Next.js API Route 代理转发，密钥保存在服务端环境变量。

## 运行

> 移动 Web 的单独启动方式见上文「移动 Web（生活助手）」一节。以下命令同时保留旧看板与 Electron 桌面端。

前置条件：使用问答/通勤等后端能力时，PA 后端与 NexusPilot 平台需已运行（NexusPilot 启动方式见其 [README](../llm_fusion_platform/README.md)）。

```bash
pnpm install
pnpm dev:web        # 浏览器开发（http://localhost:3000）
pnpm dev:desktop    # Web + Electron 桌面开发
pnpm build:web      # 生产构建
pnpm build:desktop  # Web 静态导出 + Electron 打包
```

数据库：`db:migrate` / `db:generate` / `db:studio`（Prisma 命令）。

### 移动 Web 邮箱注册

`/login` 按后端配置显示注册入口，并在需要时要求填写邀请码。提交邮箱、称呼和密码后，注册成功会自动登录并进入「今天」。

网页通过 `GET /api/auth/register` 读取注册设置，通过同路径的 `POST` 提交注册。代理沿用 `PA_API_URL` 调用 `/api/v1/auth/register`，成功时返回 201 并将会话令牌写入 HttpOnly Cookie。请求结果未知时，页面提示用户先尝试登录。

## 当前状态

- **当前产品阶段**：Personal Assistant 移动 Web 的工作安排与计划接续。规划正文见 [`docs/产品规划-工作安排与计划接续.md`](docs/产品规划-工作安排与计划接续.md)，逐项进度见 [`docs/最新规划落地对照-现有能力与差距.md`](docs/最新规划落地对照-现有能力与差距.md)。
- **早期阶段（历史）**：看板骨架（硬编码三列）、Electron IPC 数据层、类型安全链（Prisma → shared → IPC → Renderer）已完成，仅代表当时范围。
- **历史路线图**：[docs/ROADMAP.md](docs/ROADMAP.md) 是 2026-07-31 看板阶段规划，不再作为当前执行依据。

## 项目约定

- 中文 UI（看板列名、按钮文字等）
- Electron 安全基线：`contextIsolation: true`, `nodeIntegration: false`
- 数据层抽象：`lib/db/electron.ts | web.ts | index.ts` 环境检测路由
