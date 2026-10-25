# 后台运行能力验收记录（2026-10-25）

范围：在已有 assistant-api 上补齐独立 scheduler/worker 的运行能力。未引入
APScheduler/Celery/Redis/RabbitMQ；未修改 Swift、Web、NexusPilot；未提交、推送或部署。
共享 8010 实例与共享运行库本轮未改动、未重启。

## 1. 单元与契约测试

```bash
cd services/assistant-api
python -m pytest -q
```

结果：`152 passed`（离线、隔离内存/临时 SQLite、mock provider 与 fake Nexus；无真实
模型、Bark、APNs、Apple 网络调用）。

覆盖本轮关键点：

- `status` 与 `phase` 分离；终态/取消/失败/unknown 阶段一致；历史行保持 NULL；
- 新受理 check 的 `next_attempt_at=null`；仅在 `retry_wait` 安排真实重试时间；
- worker 心跳独立运行并按当前 check/outbox 续期租约；错误 owner 续期返回失败；
- 天气/路线调用于租约被抢后，旧执行者的 advisory 整事务回滚，不覆盖新 owner；
- `weather_only` 不创建 advisory、不改 latest、不调用模型，`analysis_mode` 可辨识；
- `/api/v1/commute/runtime` 未登录 401、按账号过滤 queue/latest，不返回 instance_id；
- Nexus 运行恢复先按保存的 run id 对账；只有幂等键、无 run id 时标 unknown 且不新建 run；
- Bark accepted/failed/unknown 按真实路径记录，不依赖 APNs 设备对象；租约过期未落库的
  在途发送收敛为 unknown，不盲目重发；
- 迁移对独立副本执行两次，列/表/行数正确，旧行不被回填。

新增/调整的测试文件：
`tests/test_commute_runtime_contract.py`、`tests/test_background_migration.py`、
`tests/test_run_flow.py`、`tests/test_commute_push_security.py`。

## 2. 真实 HTTP + 隔离后台进程

```bash
cd services/assistant-api
python scripts/commute_runtime_e2e.py
```

结果：`30/30 runtime checks passed`。脚本在临时 SQLite + 随机端口启动独立 API、
scheduler、worker，验证：

- 受理：手动 check 202、幂等 replay、自动 check 由 scheduler 入队；
- 执行：worker 完成 `weather_only` 与 `full`，天气快照/建议落库；
- 进度：`phase=done`、`phase_updated_at` 与 runtime `last_progress_at` 可见；
- 恢复：杀掉 worker 后把 check 租约改成过期死锁，重启 worker 后重新领取并成功；
- 计划修改：执行期间改 revision，旧 check 变为 `cancelled/plan_revision_superseded`；
- 账号隔离：其他账号 queue 为 0、latest 为 null、check 详情 404、计划列表为空；
- 结果保存：manual full 产生 advisory，latest 返回所有者数据；
- 心跳区分：worker 停止超过阈值后 `stale`，scheduler 仍 `online`；
- 结束后所有子进程被终止，临时库删除。

另运行既有通勤 HTTP 联调：

```bash
python scripts/commute_e2e_check.py
```

结果：`38/38 backend commute API checks passed`（隔离库、mock provider、无真实通知）。

基础 Nexus 运行桥接也未回归：

```bash
python scripts/e2e_check.py
```

结果：`19/19 checks passed`，包含 unknown 恢复与同一幂等键跨账号隔离。

## 3. 迁移副本验证

```bash
python -m scripts.verify_background_migration --database assistant-dev.db
```

输出要点：

- `source unchanged: True`（源库仅计算哈希，未写入）；
- 独立副本第一次升级新增 `analysis_mode/phase/phase_updated_at` 和
  `service_heartbeats`，第二次升级全部 `already_present`；
- 两次升级后行数不变，旧 check 新列保持 NULL；
- 临时副本随临时目录删除。

## 4. 未覆盖 / 处理限制

- 未发送真实 Bark/APNs，未验证手机送达；`accepted_by_bark` 只表示 Bark 受理。
- 未调用真实模型；`full` 的 AI 分支使用未配置/不可达 Nexus 时明确返回 unavailable，
  不伪造分析。
- Nexus 远端已受理、本地 run id 尚未落库的窗口没有反向查询接口，只能标 unknown；
  幂等键仅降低重复，不保证所有崩溃点可恢复。
- Bark 发送成功后、结果提交前进程中断：租约过期后 outbox 标 unknown，不重发；
  本地为 at-most-once，极端情况下可能漏一条，但不会静默重复。
- 真实 Apple 供应商与配送端到端不在本轮范围。

## 5. 交付顺序

本轮先交付接口与运行说明（[runtime-contract.md](runtime-contract.md)、
[operations.md](operations.md)）。Swift/Web 可在后续轮次按此接入进度展示；
过期样式和历史区域可并行调整，但在后台状态交付前不得提前伪造该状态。
