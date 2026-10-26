# 第二轮联合验收：iOS 应用联网证据（2026-10-26）

## 1. 测试来源（固定）

- 后端源码：`/Users/mith/Desktop/project/superapp-next-worktrees/round2`
  提交 `f6448a670030909d365a3b75b1e952d82c9d7aa5`（acceptance/round2）。
- 运行：`round2/tests/e2e/live_backend_fixture.py`，解释器 `round2/.tmp/backend-venv`，
  临时库 `round2/.tmp/live-ios/assistant.db`。
- 端口：API `8410`、控制面 `8412`、Nexus stub `8420`；共享 `8010` 全程未启动。
- 该提交包含 SQLite 连接池修复（文件库普通连接池、`check_same_thread=False`、`timeout=30`），
  本轮未修改后端代码。

启动记录（实际执行）：

```bash
cd round2
ASSISTANT_PYTHON="$PWD/.tmp/backend-venv/bin/python" \
  .tmp/backend-venv/bin/python tests/e2e/live_backend_fixture.py
# READY http://127.0.0.1:8410 control=http://127.0.0.1:8412
```

控制面（仅测试）：

```bash
curl -X POST 'http://127.0.0.1:8412/heartbeat?state=never|online|stale'
curl -X POST 'http://127.0.0.1:8412/worker'
curl -s 'http://127.0.0.1:8412/stats'
```

## 2. 应用测试

文件：`LifeAssistantUITests/LiveRuntimeUITests.swift`，测试
`testRuntimeEndToEndAgainstRound2Backend`。使用正式构建、`PA_API_BASE_URL=http://127.0.0.1:8410`，
不启用 `PA_DEMO` / `COMMUTE_DEMO`。

```bash
cd ios/LifeAssistant
xcodebuild test -project LifeAssistant.xcodeproj -scheme LifeAssistant \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' \
  -only-testing:LifeAssistantUITests/LiveRuntimeUITests/testRuntimeEndToEndAgainstRound2Backend \
  CODE_SIGNING_ALLOWED=YES CODE_SIGNING_REQUIRED=YES CODE_SIGN_IDENTITY=-
```

结果：**1/1 通过（110.6s）**。覆盖：

1. App 登录合成账号 runtime-a，Today 显示“运行验证通勤”。
2. 心跳 never 时创建检查：显示“任务已保存 / 已提交，等待处理”和“尚未收到后台执行程序的响应。”。
3. 控制面心跳 online（worker 未领取）：显示“后台执行程序最近有响应。”，任务仍等待，不显示“正在分析建议”。
4. 杀 App 再打开：恢复同一条待处理任务；控制面检查数仍为 baseline+1，没有新建检查。
5. 控制面 `/worker` 完成：App 读取建议；控制面 `/stats` 显示本次检查
   `d49afa20-…` 与建议 `f77c5e4b-…` 一一关联，且只有一条新检查。
6. 退出甲、登录乙：只显示“乙的通勤计划”，不显示甲的计划/建议。

截图：
- 等待/未报到：`/tmp/round2_waiting_final.png`
- 心跳在线但未领取：`/tmp/round2_online_final.png`
- 本次建议结果：`/tmp/round2_result_final.png`
- 切到乙账号：`/tmp/round2_account_b_final.png`

## 3. 最初失败原因与修复（同一次排查过程）

| 现象 | 原因 | 修复 |
|---|---|---|
| 没有登录邮箱输入框 | 上次运行留下“登录状态尚未验证”的离线会话，没有登录按钮 | 识别并点击“重新登录”，清掉离线会话后再登录 |
| 登录后停在“我的” | 系统“保存密码？”弹窗挡住页面 | 用 SpringBoard 的“以后”关闭系统弹窗，不用坐标 |
| 点“今天”没有切换 | 登录弹窗动画未结束 | 等待标签存在并校验 `isSelected`，必要时重试一次 |
| 找不到“等待服务器处理” | 实际阶段文案是“已提交，等待处理”（以及轮询超时后的“正在处理”） | 改为稳定的“等待处理/正在处理”断言，仍断言不出现“正在分析建议” |
| 重开没恢复任务 | 同样只匹配了错误文案 | 修正断言；恢复逻辑本身未改 |
| 切账号找不到“我的” | 详情页隐藏底部标签栏 | 切账号前先点 `nav.back` 回根页 |
| 排队时显示“分析正在进行” | 空态标题在任务未领取时就暗示已开始分析 | 仅 `phase=analyzing` 时显示“正在分析建议”，否则显示“任务已保存” |
| 检查提交失败“窗口已关闭” | 旧临时脚本固定 09:00 到达，晚间运行已过目标时间 | 使用 round2 fixture 的近未来 23:30 计划；这是测试脚本，不是产品改动 |

失败日志与截图保留在 `/tmp/round2_ui*.log` 与 xcresult 中；上述修复前均有失败记录。

## 4. 回归结果（分别报告）

- 单元测试：**135 executed / 135 passed / 0 failed**（`/tmp/round2_unit.log`）。
- 演示 UI：**7/7 通过**（`/tmp/round2_demo_ui.log`）。
- 联网 UI：**1/1 通过**（`/tmp/round2_ui9.log`）。
- 未修改后端、网页；未启动 8010；未调用真实模型/通知；验证后已停止本轮 fixture。

## 5. 仍只在演示模式验证

- 心跳年龄随页面停留自动变旧、请求失败卡片、返回详情重读、计划切换清理：
  由 `RuntimeStatusUITests` + 单元测试覆盖，未在联网后端重复。
- 甲账号未返回请求覆盖乙账号：由既有 generation 单元测试覆盖；本轮联网只验证了切号后页面不显示甲数据。
