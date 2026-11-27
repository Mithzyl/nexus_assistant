/** Render saved isolated acceptance responses with the real component; no network or model calls. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/intake-card-responses.json", import.meta.url), "utf8"));

/** Load the actual TSX and formatter using installed TypeScript; CSS does not affect markup assertions. */
function loadTypeScriptModule(url) {
  const filename = fileURLToPath(url);
  const { outputText } = ts.transpileModule(readFileSync(url, "utf8"), {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  });
  const require = createRequire(url);
  const module = { exports: {} };
  const factory = runInNewContext(`(function(require, module, exports) { ${outputText}\n})`, {}, { filename });
  factory((name) => {
    if (name === "./amap-map") return { AmapMapCanvas: () => null };
    if (name === "./route-timeline" || name === "./response-fields") return loadTypeScriptModule(new URL(name + ".tsx", url));
    if (name.endsWith(".module.css")) return { default: {} };
    if (name === "@/lib/mobile/format") return loadTypeScriptModule(new URL("../lib/mobile/format.ts", import.meta.url));
    return require(name);
  }, module, module.exports);
  return module.exports;
}

const { ApiOperationCard } = loadTypeScriptModule(new URL("../components/mobile/api-operation-card.tsx", import.meta.url));

/** Confirm an explicit empty list suppresses cards after JSON persistence without losing its receipt. */
test("saved text-only response renders no card and retains the execution receipt", () => {
  const context = JSON.parse(JSON.stringify(fixture.text_only.pending_context));
  const before = structuredClone(context);
  assert.equal(context.execution_result.data.status, "done");
  assert.equal(renderToStaticMarkup(createElement(ApiOperationCard, { context })), "");
  assert.deepEqual(context, before);
  assert.deepEqual(context.execution_result, fixture.text_only.pending_context.execution_result);
});

/** Preserve the saved declared card, including its Chinese todo state and both timing fields. */
test("saved nonempty cards render their declared fields without a legacy result card", () => {
  const html = renderToStaticMarkup(createElement(ApiOperationCard, { context: fixture.with_cards.pending_context }));
  assert.match(html, /aria-label="助手卡片"/);
  assert.match(html, /会前整理报销资料/);
  assert.match(html, /截止时间/);
  assert.match(html, /提醒时间/);
  assert.match(html, /未完成/);
  assert.doesNotMatch(html, /aria-label="操作结果"/);
});

/** Model an older saved response by omitting only cards; the real execution receipt must remain readable. */
test("legacy response without cards still renders its execution receipt", () => {
  const { cards, ...context } = fixture.text_only.pending_context;
  const before = structuredClone(context);
  const html = renderToStaticMarkup(createElement(ApiOperationCard, { context }));
  assert.match(html, /aria-label="操作结果"/);
  assert.match(html, /本次结果/);
  assert.match(html, /会前整理报销资料/);
  assert.deepEqual(context, before);
});

/** Check available failure actions for committed, uncommitted and partially unknown business effects. */
test("failed final answers only offer editing after confirmed non-write", () => {
  const { IntakeFailureActions } = loadTypeScriptModule(new URL("../components/mobile/intake-failure-actions.tsx", import.meta.url));
  for (const state of ["applied", "uncertain", "not_applied"]) {
    const context = {write_state: state, applied_receipts: state === "not_applied" ? [] : [{summary: "创建评审日程"}]};
    const html = renderToStaticMarkup(createElement(IntakeFailureActions, {context, onEdit() {}, onCheck() {}}));
    assert.equal(html.includes("改改再发"), state === "not_applied");
    assert.equal(html.includes("/today"), state !== "not_applied");
    assert.equal(html.includes("创建评审日程"), state !== "not_applied");
    assert.equal(html.includes("查一下结果"), state === "uncertain");
  }
});

/** Render route text and its default icon when a saved measurement lacks geometry. */
test("route without shape or picture is readable with no map action", () => {
  const card = {
    card_type: "timeline_route", kind: "info", status: "info", title: "当天怎么走",
    blocks: [{ type: "timeline", items: [{ title: "步行 8 分钟到中山路站" }] }],
  };
  const html = renderToStaticMarkup(createElement(ApiOperationCard, { context: { cards: [card] } }));
  assert.match(html, /步行 8 分钟到中山路站/);
  assert.match(html, /<svg/);
  assert.doesNotMatch(html, /查看路线地图/);
});

/** Render populated progress groups and one nested route, even without media. */
test("progress renders current sections and its single text time line", () => {
  const card = {
    card_type: "long_task_progress", kind: "info", status: "info", title: "搬家进展", blocks: [],
    payload: {
      latest_changes: ["车队可在周五到场"], next_decisions: ["选择上午或下午"],
      embedded_card: { card_type: "timeline_route", title: "搬家步骤",
        blocks: [{ type: "timeline", items: [{ title: "整理书箱" }] }] },
    },
  };
  const html = renderToStaticMarkup(createElement(ApiOperationCard, { context: { cards: [card] } }));
  assert.match(html, /新变化.*车队可在周五到场/);
  assert.match(html, /等你拍板.*选择上午或下午/);
  assert.match(html, /搬家步骤.*整理书箱/);
  assert.doesNotMatch(html, /正在关注/);
});

/** Render a location-only initial group and leave other group details hidden until selected. */
test("map lists groups and falls back to text when the selected group has no points", () => {
  const group = (group_id, label, title, distance_meters) => ({
    group_id, label, distance_meters, shape: { segments: [] }, markers: [],
    blocks: [{ type: "timeline", items: [{ title }] }],
  });
  const card = { kind: "info", status: "info", title: "周末路线", card_type: "multi_route_map", blocks: [],
    payload: { groups: [group("0","周六","中山路站", null), group("1","周日","柳江公园", 900)] } };
  const html = renderToStaticMarkup(createElement(ApiOperationCard, { context: { cards: [card] } }));
  assert.match(html, /aria-pressed="true"/);
  assert.match(html, /0.9 公里/);
  assert.match(html, /中山路站/);
  assert.doesNotMatch(html, /柳江公园/);
  assert.doesNotMatch(html, /地图仅标出已知地点/);
});

/** Keep legacy dated trips readable without migrating any saved fields. */
test("dated travel history shows date, separate categories, overnight chips and bare transitions", () => {
  const legacy = { kind: "plan", status: "suggestion", title: "周末出行", blocks: [{
    type: "timeline", items: [
      { title: "柳州到佛山", time: "12/26 周六", category: "出发", description: "抵达后逛岭南天地。宿禅城。" },
      { title: "12/28 周一 · 佛山到中山", category: "转场+游玩", connector: "约一小时车程",
        description: "参观孙中山故里；附近有宿舍。", fields: [
          { label: "住宿地点", value: "中山" }, { label: "已订票", value: true },
        ] },
    ],
  }] };
  const html = renderToStaticMarkup(createElement(ApiOperationCard, { context: { cards: [legacy] } }));
  assert.equal((html.match(/data-route-node/g) ?? []).length, 2);
  assert.match(html, /data-weekend="true">12\/26 周六/);
  assert.match(html, /data-weekend="false">12\/28 周一/);
  assert.match(html, /data-route-transition="true"[\s\S]+?约一小时车程/);
  assert.match(html, />转场<.*>游玩</);
  assert.match(html, /住 禅城.*住 中山/);
  assert.match(html, /附近有宿舍/);
  assert.match(html, /已订票<\/dt><dd>是/);
  assert.doesNotMatch(html, /宿禅城|<dt>住宿地点|12\/28 周一 ·/);
});

/** Legacy business timelines and untrusted image URLs must not pick up the itinerary treatment. */
test("only recognized itineraries opt into new layout and trusted thumbnails", () => {
  const card = { kind: "plan", status: "suggestion", title: "考勤历史", blocks: [{
    type: "timeline", items: [{ title: "工作", time: "1/7" }, { title: "会议", time: "1/8" }],
  }] };
  assert.doesNotMatch(renderToStaticMarkup(createElement(ApiOperationCard, { context: { cards: [card] } })), /data-route-node/);
  const explicit = { ...card, card_type: "timeline_route", blocks: [{ type: "timeline", items: [
    { title: "游玩", image: { src: "https://unverified.invalid/photo.jpg", alt: "外部图片" } },
    { title: "保存的图片", time: "12/28 周一 08:30", image: { src: "/image-attachments/example-id/original", alt: "行程资料" } },
  ] }] };
  const html = renderToStaticMarkup(createElement(ApiOperationCard, { context: { cards: [explicit] } }));
  assert.doesNotMatch(html, /unverified.invalid/);
  assert.match(html, /\/api\/pa\/image-attachments\/example-id\/thumbnail/);
  assert.match(html, />08:30</);
});
