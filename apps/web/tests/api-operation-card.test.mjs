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
