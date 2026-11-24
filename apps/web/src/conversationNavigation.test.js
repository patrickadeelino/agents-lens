import { strict as assert } from "node:assert";
import { test } from "node:test";
import { inspectionUrl, resolveInspectedRun } from "./conversationNavigation.js";

test("inspection only addresses a turn belonging to the conversation", () => {
  const thread = { turns: [{ run_id: "first" }, { run_id: "second" }] };
  assert.equal(resolveInspectedRun(thread, "first"), "first");
  assert.equal(resolveInspectedRun(thread, null), null);
  assert.throws(() => resolveInspectedRun(thread, "unrelated"));
});

test("closing inspection keeps canonical or provisional navigation", () => {
  const canonical = "http://localhost/?thread_id=thread&run_id=first#action";
  assert.equal(inspectionUrl(canonical, "second").search, "?thread_id=thread&run_id=second");
  assert.equal(inspectionUrl(canonical, null).search, "?thread_id=thread");
  assert.equal(inspectionUrl("http://localhost/?run_id=root", null, "root").search, "?run_id=root");
});
