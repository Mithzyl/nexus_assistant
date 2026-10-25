# 额外模型流程失败记录（2026-10-25，Web 线程）

## 1. 结论摘要

`tests/e2e/acceptance_model_flows.py` 在“网页点击重新分析 → 创建 check”这一步**间歇性**收到
HTTP 500。失败时服务器没有创建 check，也没有生成建议；不是网页把结果显示错，也不是模型
结果校验失败。

同一命令、同一提交、同一份后端源码下出现：

| 运行 | Python | SQLAlchemy | 结果 |
|---|---|---|---|
| 1 | 3.12.12 | 2.1.6 | 失败：`no such savepoint: sa_savepoint_1` |
| 2 | 3.12.12 | 2.1.6 | 通过：23/23 |
| 3 | 3.12.12 | 2.1.6 | 失败：同一 savepoint 错误 |
| 4 | 3.11.9 | 2.0.51 | 通过：23/23 |
| 5 | 3.11.9 | 2.0.51 | 失败：同一 savepoint 错误 |

因此不能把第 2 或第 4 次通过当作稳定通过；额外模型流程目前**未稳定通过**，等待后端修复。
本文只提供事实与现场，不修改后端代码。

## 2. 完整执行命令

在 worktree 根目录：

```bash
cd /Users/mith/Desktop/project/superapp-next-worktrees/web
PATH=/Users/mith/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin:$PATH \
ASSISTANT_PYTHON=/Users/mith/Desktop/project/superapp-next/.tmp/devvenv/bin/python \
python3 tests/e2e/acceptance_model_flows.py
```

换 SQLAlchemy 2.0.51 复验时，只替换 `ASSISTANT_PYTHON`：

```bash
ASSISTANT_PYTHON=/Users/mith/Desktop/project/superapp-next-worktrees/web/.tmp/sqlalchemy20-venv/bin/python \
python3 tests/e2e/acceptance_model_flows.py
```

脚本默认端口：API `8411`、Web `3411`、Nexus stub `8421`；启动前检查端口占用。

## 3. 代码与环境

- Web 提交：`4238ae60113e470e0013a2f3c6d09b2b2026f031`（`web/dev`）
- 已验收基线：`26095a8b24e39bb619007c57788fe199ccd28aa7`
- 实际加载的后端源码（修复测试脚本后打印并校验）：
  `/Users/mith/Desktop/project/superapp-next-worktrees/web/services/assistant-api/src/assistant_api`
- 后端源码提交：`26095a8b24e39bb619007c57788fe199ccd28aa7`
- 关键文件 SHA-256：
  `commute/services/scheduling.py = a932e9f8a5529aeab2615ae32fcdd592f8c11fd4ce569ac49ec5cf73f0cbb70c`
- 第一次失败时，解释器的 editable install 实际指向主检出
  `/Users/mith/Desktop/project/superapp-next/services/assistant-api/src`；两者的源码在该提交上
  逐文件相同（仅 `__pycache__` 不同）。之后测试脚本已在子进程中强制
  `PYTHONPATH=<worktree>/services/assistant-api/src` 并打印实际路径；间歇性失败仍然复现。
- 依赖（失败与通过使用同一套依赖，仅换版本复验）：

| 运行 | Python | SQLAlchemy | aiosqlite | SQLite |
|---|---|---|---|---|
| 3.12 环境 | 3.12.12 | 2.1.6 | 0.22.1 | 3.50.4 |
| 3.11 环境 | 3.11.9 | 2.0.51 | 0.22.1 | 3.45.1 |

## 4. 数据库与并发

- 数据库：`/Users/mith/Desktop/project/superapp-next-worktrees/web/.tmp/model-flows/assistant.db`
- 每次运行由 `acceptance_model_flows.py` 重新创建临时目录和 SQLite 文件；不复用用户库。
- 测试只启动一个 API、一个 Web、一个 Nexus stub；端口先检查，未重复启动。
- 测试父进程只在请求之外读取 DB；未在请求处理中清理或写入数据库。
- 失败后的库状态：`commute_plans=1`，`commute_instances=0`，`commute_checks=0`。
  说明请求在 `ensure_instance()` 内失败，未留下部分实例/检查。
- 后端 SQLite 引擎使用 `StaticPool` 单连接（`infrastructure/database.py`），同时 Web 详情页
  会并行读取计划、计划列表、runtime、天气和最新建议。是否由同一连接上的并发事务交错触发
  savepoint 问题，请后端结合代码确认；本文只陈述该事实。

## 5. 失败前的操作顺序

1. 创建临时 SQLite schema 与合成账号。
2. 启动 Nexus stub。
3. 启动 API 与生产构建的 Web。
4. 通过 API 创建合成通勤计划。
5. 打开 `/me/commute/<plan_id>`，页面读取计划/列表/runtime/天气/建议。
6. Playwright 点击“重新分析”，`POST /api/v1/commute-plans/<plan_id>/checks` 返回 500。
7. API 返回 500 后，页面没有可继续点击的“重新分析”按钮；Playwright 在该步骤等待超时。数据库中没有 check。

## 6. 原始错误

完整日志见：

- `processes.log`：第一次 3.12 / SQLAlchemy 2.1.6 失败
- `processes-second-failure.log`：强制 worktree 路径后 3.12 / 2.1.6 再次失败
- `processes-sqlalchemy20-failure.log`：3.11 / SQLAlchemy 2.0.51 失败
- `acceptance-*.log`：对应浏览器命令输出

关键栈：

```text
File ".../commute/router.py", line 170, in create_check
  created, replayed = await check_service.create_manual_check(
File ".../commute/services/checks.py", line 153, in create_manual_check
  instance = await ensure_instance(db, plan, now=now, allow_non_scheduled_day=True)
File ".../commute/services/scheduling.py", line 79, in ensure_instance
  async with db.begin_nested():
...
sqlalchemy.exc.OperationalError:
(sqlite3.OperationalError) no such savepoint: sa_savepoint_1
[SQL: RELEASE SAVEPOINT sa_savepoint_1]
```

`instance` 插入和 check 创建都没有落库；没有伪造成功结果。

## 7. 测试侧已做的修正

`acceptance_model_flows.py` 现在会：

- 在 `BASE_ENV` 中把 `PYTHONPATH` 指向本 worktree 的 `services/assistant-api/src`；
- 启动前打印 Python/SQLAlchemy/aiosqlite/SQLite 版本与 `assistant_api.__file__`；
- 若实际导入的不是本 worktree，直接报错退出，避免误测其他检出。

这是测试隔离修正，不是后端修复。修正后流程仍间歇失败，故不宣称额外模型流程通过。

## 8. 需要后端确认/修复的点

- `create_manual_check()` → `ensure_instance()` 的 `db.begin_nested()` 在 SQLite 单连接池、
  页面并行请求下是否会与其他事务交错，导致 `RELEASE SAVEPOINT` 失败。
- 请给出修复提交与稳定测试地址；后端修复就绪后，Web 线程再用同一命令重跑两条模型流程
  （有效结果、unknown 结果），并保留“模拟数据必须显示开发/测试标签”的断言。

## 9. 安全

本文和日志未包含真实密码、令牌、Cookie 或用户数据；测试账号与计划均为临时合成数据。
测试未启动共享 8010，未调用真实模型，未发送通知。
