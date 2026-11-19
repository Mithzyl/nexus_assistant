# SuperApp

## 当前阶段（2026-11-09 更新）

- **现行产品规划**：[`docs/产品规划-工作安排与计划接续.md`](docs/产品规划-工作安排与计划接续.md)。这是当前唯一的产品规划依据：阶段一为统一输入、偏好默认项和下一步；阶段二为持续关注、自动安排和简报复盘；阶段三建议交付场景能力包及 iOS 便捷入口。旧规划原文保留作历史参考。
- **当前交付重点**：先完成全局随手输入、记忆驱动默认项和今天页下一步；现有事项、日程、提醒及通勤继续复用。网页独立可用，iOS 基本业务继续维护，便捷入口增强按阶段三安排。
- **开发顺序**：先修复已有真实任务发现的相关接口选择、JSON 输出和冲突反馈问题；按现行规划阶段一至三推进，不重新建立已有业务模型。具体顺序见 [`docs/开发安排.md`](docs/开发安排.md)。
- **当前进度**：后端已有事项、独立日程/待办、提醒、同步接口、补充和跟进。本机已加载新 Agent 后端，基本业务执行和同 Agent 续接已有真实隔离证据；最新模型完整路线任务和新三阶段产品验收尚未通过。逐项对照见 [`docs/最新规划落地对照-现有能力与差距.md`](docs/最新规划落地对照-现有能力与差距.md)，不要用本文或旧版演示判断功能已完成。
- **文档入口**：[`docs/文档目录.md`](docs/文档目录.md)。

> 以下“看板 + LLM 聊天”内容是该仓库早期阶段的实现，保留作历史说明，不代表当前产品范围。

**早期阶段简介（历史）**：个人超级应用：看板（Kanban）+ LLM 聊天（带 function calling）。

**LLM 底层复用 [NexusPilot](https://github.com/Mithzyl/llm_nexus_pilot) LLM 平台**（本地部署，HTTP API 接入）。模型调用、会话/消息存储、运行与任务审计、费用与调用证据由平台承担，本仓库不重复自研 LLM 基础设施。早期阶段本仓库专注看板领域数据、界面与工具执行；当前阶段的移动 Web 与 PA 后端见下文。

## 移动 Web（生活助手）

`apps/web` 现在同时承载 Personal Assistant 移动 Web 客户端和旧版看板：

- 移动入口 `/today`，主要页面为 `/matters`、`/ask` 和 `/me`；实际页面版本应按对应 Web worktree 核对，不再用旧 main 的演示界面推断后端能力。
- 浏览器访问同源转发层，Assistant 保存会话和业务。Nexus 凭据与模型绑定只在服务端。
- Assistant 将相关业务说明交给 Nexus，同一 Agent 的完整正文经平台执行后追加实际结果，最终投影为回答和卡片；协议与范围见[当前后端接入说明](docs/backend-integration/27-接口复核与AI业务调用说明.md)。
- 后端运行入口见[Assistant API README](services/assistant-api/README.md)。历史 Web 验收记录见[移动 Web 实现与验证](docs/移动Web实现与验证.md)，不代表当前客户端或新接入已验收。
- 旧版看板保留在 `/board`，只属于历史产品范围。

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
- 当前 Assistant 的模型通信经后端 App 身份接入 Nexus；下方看板示意只描述早期实现。

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

前置条件：使用问答/通勤等后端能力时，PA 后端与 NexusPilot 平台需已运行（NexusPilot 启动方式见其[仓库说明](https://github.com/Mithzyl/llm_nexus_pilot)）。

```bash
pnpm install
pnpm dev:web        # 浏览器开发（http://localhost:3000）
pnpm dev:desktop    # Web + Electron 桌面开发
pnpm build:web      # 生产构建
pnpm build:desktop  # Web 静态导出 + Electron 打包
```

数据库：`db:migrate` / `db:generate` / `db:studio`（Prisma 命令）。

## 当前状态

- **当前产品阶段**：生活助手的统一输入、下一步与持续服务；按三个阶段逐步交付。规划正文见 [`docs/产品规划-工作安排与计划接续.md`](docs/产品规划-工作安排与计划接续.md)，逐项进度见 [`docs/最新规划落地对照-现有能力与差距.md`](docs/最新规划落地对照-现有能力与差距.md)。
- **早期阶段（历史）**：看板骨架（硬编码三列）、Electron IPC 数据层、类型安全链（Prisma → shared → IPC → Renderer）已完成，仅代表当时范围。
- **历史路线图**：[docs/ROADMAP.md](docs/ROADMAP.md) 是 2026-07-31 看板阶段规划，不再作为当前执行依据。

## 项目约定

- 中文 UI（看板列名、按钮文字等）
- Electron 安全基线：`contextIsolation: true`, `nodeIntegration: false`
- 数据层抽象：`lib/db/electron.ts | web.ts | index.ts` 环境检测路由
