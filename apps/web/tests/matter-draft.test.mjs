import assert from "node:assert/strict";
import test from "node:test";

import {
  affectedScheduleSummary,
  changeReasonText,
  classifyMatterDraft,
  conflictReasonText,
  conflictHasUnverifiedRoute,
  deadlineTiming,
  isRecordChangedError,
  manualEditSelection,
  mergeIntakeHistory,
  optionConsequences,
  summarizeMatterChange,
  todayChangeSummary,
} from "../lib/mobile/matter-draft.mjs";

test("direct schedule draft does not require a matter title", () => {
  const kind = classifyMatterDraft({
    kind: "update",
    payload: {
      title: null,
      source_text: "直接编辑",
      schedule: { schedule_id: "schedule-1", title: "面谈" },
      todos: [],
    },
  });
  assert.equal(kind, "schedule");
});

test("direct todo draft stays separate from schedule editing", () => {
  const kind = classifyMatterDraft({
    kind: "update",
    payload: {
      title: null,
      source_text: "直接编辑",
      schedule: null,
      todos: [{ todo_id: "todo-1", title: "准备材料" }],
    },
  });
  assert.equal(kind, "todo");
});

test("manual edits clear a previous option and risk acknowledgement", () => {
  assert.deepEqual(manualEditSelection(), { selectedOption: null, acknowledge: false });
});

test("option B summary names the neighbour adjustment and new todo", () => {
  const lines = optionConsequences(
    { option_id: "B", detail: "调整相邻安排" },
    { todos: [{ todo_id: "todo-1", title: "准备材料" }] },
    {
      previous_adjustment: { shift_seconds: -600 },
      todos: [
        { todo_id: "todo-1", title: "准备材料" },
        { todo_id: null, title: "联系相关方" },
      ],
    },
  );
  assert.match(lines.join(""), /提前相邻安排约 10 分钟/);
  assert.match(lines.join(""), /新增待办「联系相关方」/);
});

test("revert reason and deadline wording use user-facing labels", () => {
  assert.equal(changeReasonText("later_changes"), "这条安排后来又被修改，不能直接撤回。");
  assert.equal(deadlineTiming("2026-10-26T09:00:00+08:00", "2026-10-27").label, "已逾期");
  assert.equal(deadlineTiming("2026-10-27T18:00:00+08:00", "2026-10-27").label, "今天到期");
});

test("today change summary never exposes the backend change type", () => {
  assert.equal(
    todayChangeSummary({
      change_type: "update",
      summary: "update / 面谈 / 2026-10-27T08:00:00Z / 陆家嘴",
    }),
    "修改：面谈 / 2026-10-27T08:00:00Z / 陆家嘴",
  );
});

test("unverified travel remains visible without a hard conflict", () => {
  assert.equal(
    conflictHasUnverifiedRoute({
      has_conflict: false,
      eta_note: "travel_unverified:coordinates_missing",
      conflicts: [],
    }),
    true,
  );
});

test("server affected schedule names both matters and before/after", () => {
  const affected = {
    matter_id: "matter-b",
    matter_title: "另一个事项",
    schedule_id: "schedule-b",
    title: "牙医",
    before_starts_at: "2026-10-27T05:00:00Z",
    after_starts_at: "2026-10-27T04:50:00Z",
  };
  const summary = affectedScheduleSummary(affected);
  assert.match(summary, /另一个事项/);
  assert.match(summary, /牙医/);
  assert.match(summary, /调整为/);

  const lines = optionConsequences(
    { option_id: "B", detail: "调整相邻安排", affected: [affected] },
    { todos: [] },
    { previous_adjustment: { shift_seconds: -600 }, todos: [] },
  );
  assert.match(lines.join(""), /另一个事项/);
});

test("record changed errors trigger reread and creation revert remains unsupported", () => {
  assert.equal(isRecordChangedError("todo_changed"), true);
  assert.equal(isRecordChangedError("schedule_changed"), true);
  assert.equal(isRecordChangedError("option_not_executable"), false);
  assert.equal(changeReasonText("creation_revert_unsupported"), "暂不支持撤回新建事项。");
});

test("affected history summary uses the source matter instead of raw payload", () => {
  const summary = summarizeMatterChange({
    relation: "affected",
    source_matter_id: "source-matter",
    source_matter_title: "来源事项",
    matter_id: "source-matter",
    before: {
      adjacent_schedules: [
        {
          schedule_id: "schedule-1",
          starts_at: "2026-10-27T07:00:00Z",
          ends_at: "2026-10-27T08:00:00Z",
        },
      ],
    },
    after: {
      adjacent_schedules: [
        {
          matter_id: "affected-matter",
          matter_title: "受影响事项",
          schedule_id: "schedule-1",
          title: "面谈",
          starts_at: "2026-10-27T07:10:00Z",
          ends_at: "2026-10-27T08:10:00Z",
        },
      ],
    },
  });
  assert.match(summary, /来源事项/);
  assert.match(summary, /面谈/);
  assert.doesNotMatch(summary, /update/);
  assert.doesNotMatch(summary, /未设置/);
});

test("route conflict reasons are translated and never expose codes", () => {
  assert.match(conflictReasonText("eta_short", "available"), /时间可能不够/);
  assert.match(conflictReasonText("gap_short_route_unverified", "unverified"), /尚未核实/);
  assert.doesNotMatch(conflictReasonText("next_too_close", "available"), /next_too_close/);
});

test("unified history keeps server rows and only non-duplicate local fallback", () => {
  const merged = mergeIntakeHistory(
    [
      {
        local_run_id: "run-1",
        request_text: "今天天气",
        answer: "多云",
        clarify: null,
      },
    ],
    [
      {
        id: "old-1",
        localRunId: "run-1",
        question: "今天天气",
        answer: "多云",
        createdAt: "2026-10-27T00:00:00Z",
      },
      {
        id: "old-2",
        question: "旧问题",
        answer: "旧回答",
        createdAt: "2026-10-26T00:00:00Z",
      },
    ],
  );
  assert.equal(merged.length, 2);
  assert.equal(merged[0].local_run_id, "run-1");
  assert.equal(merged[1].id, "old-2");
});
