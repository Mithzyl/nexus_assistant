# 移动 Web 工作安排真实接入：实现与验证

> 网页分支：`web/dev`；真实后端口径：`backend/dev 55cb0fa9`。
> 文档依据：主分支 `429f757b` 及 `docs/backend-integration/17-工作安排与计划接续-后端实现与网页接入.md` 的 55cb 版本。
> 未合并 main、未推送；后端代码未复制进网页目录。

## 1. 真实接入

- 代理只新增 `matters`、`today`、`reminders` 三个明确范围，仍使用 `/api/pa` 和 httpOnly 会话，不改成任意路径。
- `GET /matters?group=all&limit=20&cursor=` 读取真实分组列表，按 `next_cursor` 追加。
- 详情读取 `matter_id/title/goal/status/version/next_step/latest_progress/schedules/todos/progress/reminders`；不显示内部版本号，不从文字猜下一步时间。
- `PATCH /matters/{id}` 带 `expected_version`；成功后重新读取。标题编辑支持直接入口；时间/地点没有直接写接口，通过“补充进展/整理”生成真实草稿再确认。
- 待办调用 complete/reopen；归档调用 archive（`cancel_reminders=false`，产品未定不擅自取消）；删除为服务器软删。
- 今天读取 `GET /today?date=`，合并 schedules/reminders 按时间排序；失败显示读取失败，不显示成空日程；`pending_drafts` 只显示数量并跳转问助手，不制造假卡。
- 问助手新增显式“整理成事项”入口，使用 `POST /matters/intake`；若服务器返回 `intent=question` / `draft=null`，不打开空面板，而是把同一句按普通问题提交到 `/asks`，不创建事项；`unclear` 显示补充提示。事项详情补充进展使用 `POST /matters/{id}/intake`。
- 问助手通过 `GET /matters/drafts?status=preview` 恢复服务器草稿，不只依赖页面内存；不自动反复弹出面板。
- 草稿面板接入 `GET/PATCH /matters/drafts/{id}`、`select-matter`、`apply-option`、`confirm`、`cancel` 能力（cancel 未强绑按钮，关闭保留草稿）。方案选择只更新预览，确认才写正式记录。
- 确认使用稳定 `Idempotency-Key` 与当前 `expected_version`；`acknowledge_conflict` 只表示接受风险，有方案时必须先选 A/B/C；超时保留同一标识。确认结果读取 `applied/conflict/change`，提示实际写入的日程、提醒与残余风险。
- 安排卡片提供结构化“调整”面板，预填标题/开始/结束/地点；保存后通过固定事项 intake 生成草稿，再在草稿中确认，不直接改写正式日程。待办的截止与提醒触发时间分别编辑，也走同一草稿确认路径。
- 撤回使用本次 confirm 返回的 change_id + 当前 version；刷新后没有变更历史列表，故不显示可撤回入口。
- 同步文案按 `source/sync_status` 区分应用内、待 iOS、苹果来源副本；提醒只表示已保存，不显示已送达或已写回 iPhone。

## 2. 页面与交互

- 底栏仍为今天／事项／问助手／我的；改为带间距、圆角和毛玻璃的悬浮导航，内容底部留出安全区。
- 事项三组与三行摘要（标题、最新进展、下一步）保持；详情四块保持，待办完成使用绿色与“已完成”文字。
- 草稿面板明确待确认、提交中、失败、结果未知、过期/已变化；关闭有未保存修改时询问“继续编辑/放弃并关闭”，关闭不提交。
- 实验室 `/me/lab/plan` 继续隔离本机预览；正式列表不再使用 WorkMatter。预览修复了动态目标时间、线上冲突、撤回入口提前出现和账号切换写入顺序，并补了左滑删除入口和个人经验可发现删除按钮。
- PWA 清单、图标、standalone 保持。

## 3. 验证

```bash
ASSISTANT_PYTHON=<backend/dev 55cb venv> \
E2E_BACKEND_SRC=<backend/dev 55cb>/services/assistant-api/src \
pnpm --filter @superapp/web test:e2e
```

- `tsc --noEmit`：通过。
- `next build`：通过。
- 主流程：90/90；后台状态：23/23；真实事项/草稿：38/38；0 pageerror。
- 实际启动的后端：本 worktree 的 `.tmp/backend-74927adb`（已切到 `55cb0fa9`），独立 SQLite/端口，不使用共享 8010。
- 浏览器覆盖：真实今天、事项分组、详情四块、标题编辑/重载、待办完成、草稿恢复/关闭警告/方案应用/确认、删除/重开、账号切换、实验室隔离、360px。

## 4. 具体剩余问题

1. **普通问答自动识别事项**：后端目前是 `/asks` 与 `/matters/intake` 两个入口，没有自动分流契约；网页提供显式“整理成事项”，未把普通回答强行建成事项。自动衔接仍属产品/后端待定。
2. **今天冲突/临近节点/通勤异常卡**：`/today` 只返回 schedules、reminders、pending_drafts 数量，没有冲突或 ETA 字段；当前不伪造变化卡，`pending_drafts` 只跳转问助手。
3. **日程直接编辑**：已有预填标题/起止/地点的结构化调整面板，但没有直接 PATCH 日程的网页接口；保存时通过固定事项 intake 生成草稿，再由草稿修改并确认。
4. **通勤建议与具体日程关联**：today 没有 matter/schedule 与通勤建议的关联字段；当前保留独立通勤卡并明确未自动关联。
5. **撤回历史列表**：后端没有变更历史查询；只能撤回本次确认返回的 change_id，刷新后不显示“可撤回”。
6. **停止跟进/归档**：后端 archive 默认不取消提醒，产品时点未定；网页不擅自传 `cancel_reminders=true`。
7. **提醒送达、苹果回写**：仅按记录状态展示，不宣称通知送达或 iPhone 已写入；真机安装、软键盘、主屏幕启动未验证。
8. **草稿取消按钮**：关闭面板保留草稿，未额外暴露“取消草稿”操作；服务器 cancel 接口已存在，但产品未要求本次入口。

## 5. 结论

本轮把正式事项、草稿、今天和补充进展从本机预览推进到了真实 API；预览仍保留在实验室且与正式数据隔离。仍有 1、2、4、5 等需要后端/产品补齐的功能，不以 90/23/38 的测试数字代替六条产品验收。
