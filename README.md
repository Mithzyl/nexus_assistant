# SuperApp

个人超级应用：看板（Kanban）+ LLM 聊天（带 function calling）。

**LLM 底层复用 [NexusPilot](../llm_fusion_platform/) LLM 平台**（本地部署，HTTP API 接入）。模型调用、会话/消息存储、运行与任务审计、费用与调用证据由平台承担，本仓库不重复自研 LLM 基础设施。本仓库专注看板领域数据、界面与工具执行。

## 移动 Web（生活助手）

`apps/web` 现在同时承载 Personal Assistant 移动 Web 客户端和旧版看板：

- 移动入口：`/` → `/today`，四个 Tab：今天、事务、助手、我的。页面复用 `services/assistant-api`，浏览器只访问同源转发层。
- 旧版看板：保留在 `/board`；Electron 开发入口已指向该路由。
- 会话令牌保存在 httpOnly Cookie，不进入浏览器 JavaScript；AI 平台令牌与模型绑定只在服务端。
- 事务、退款等页面在明确开启演示模式后显示本地示例；未开启时显示真实空状态和能力说明。
- 启动、接口对应表、验证结果与剩余依赖见 [`docs/移动Web实现与验证.md`](docs/移动Web实现与验证.md)。
- 通勤分析、路线来源、会话 Cookie 与本轮验证见 [`docs/移动Web通勤分析推进记录.md`](docs/移动Web通勤分析推进记录.md)。

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
│   ├── app/      # 页面（当前：看板）
│   ├── components/board/   # 看板组件
│   ├── lib/db/   # 数据层：electron.ts | web.ts | index.ts 环境检测路由
│   └── prisma/   # Prisma + SQLite（看板领域数据）
├── desktop/      # Electron 31 壳，主进程持有 SQLite（Prisma），经 IPC 暴露
└── shared 包: packages/shared  # 跨端共享类型（Board/Column/Card）
```

- `apps/web` 在浏览器中运行：看板数据走本地存储 stub（Phase 2 规划 IndexedDB）
- `apps/web` 嵌入 Electron 运行时：看板数据走 IPC → 主进程 Prisma → SQLite
- LLM 功能统一走 NexusPilot HTTP API（经 Next.js API Route 代理，见下）

## 架构

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

前置条件：NexusPilot 平台已本地运行（MySQL + MinIO + API），启动方式见其 [README](../llm_fusion_platform/README.md)。

```bash
pnpm install
pnpm dev:web        # 浏览器开发（http://localhost:3000）
pnpm dev:desktop    # Web + Electron 桌面开发
pnpm build:web      # 生产构建
pnpm build:desktop  # Web 静态导出 + Electron 打包
```

数据库：`db:migrate` / `db:generate` / `db:studio`（Prisma 命令）。

## 当前状态

- **Phase 1（完成）**：看板骨架（硬编码三列），Electron IPC 数据层，类型安全链（Prisma → shared → IPC → Renderer）
- **规划**：见 [docs/ROADMAP.md](docs/ROADMAP.md)

## 项目约定

- 中文 UI（看板列名、按钮文字等）
- Electron 安全基线：`contextIsolation: true`, `nodeIntegration: false`
- 数据层抽象：`lib/db/electron.ts | web.ts | index.ts` 环境检测路由
