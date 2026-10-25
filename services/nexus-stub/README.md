# nexus-stub

`/api/v1/app/*` 的可执行桩，用来在不启动 NexusPilot 的前提下联调 Personal Assistant。

它是**契约夹具**，不是 NexusPilot 实现：全部状态在内存，没有数据库、没有 Agent、没有模型。
请求体用 `extra="forbid"`，任何多出来的字段直接 422——包括 `user_id`、`principal_id`，
这样"客户端不能声明身份"这条规则会被机器挡住，而不是靠人记。

## 运行

```bash
STUB_APP_TOKEN=<与 ASSISTANT_NEXUS_APPLICATION_TOKEN 相同> \
  ../assistant-api/.venv/bin/python -m uvicorn app:app --port 8001
```

## 已实现

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/app/principals/resolve` | 按外部用户生成确定性 principal |
| POST | `/api/v1/app/runs` | 幂等创建运行 |
| GET | `/api/v1/app/runs/{run_id}` | 按头部用户校验归属 |
| POST | `/api/v1/app/runs/{run_id}/agent-workflows` | 只认 `personal_assistant_default` |
| GET | `/api/v1/app/agent-workflows/{id}/events` | 真实分块 SSE，支持 `Last-Event-ID` |
| POST | `/api/v1/app/agent-workflows/{id}/cancel` | 置为 cancelled |
| GET | `/api/v1/app/agent-workflows/{id}/result` | 未终态返回 409 |
| POST | `/api/v1/app/memory-retrievals` | 返回两条带来源的记忆 |
| GET/DELETE | `/_debug/calls` | 记录客户端实际发了什么，供测试断言"没发什么" |

## 用途边界

- 可用于：联调、契约演示、CI 里的真实 HTTP 端到端。
- 不可用于：性能结论、真实模型行为、权限矩阵验收。这些必须在真实 NexusPilot 上跑。
