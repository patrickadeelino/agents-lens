import { strict as assert } from "node:assert";
import { test } from "node:test";
import { groupConversations, inspectionUrl, resolveInspectedRun } from "./conversationNavigation.js";

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

test("groups use full workspace paths and preserve activity order", () => {
  const threads = [
    { workspace: "/labs/api", root_run_id: "one" },
    { workspace: "/other/api", root_run_id: "two" },
    { workspace: "/labs/api", root_run_id: "three" },
    { root_run_id: "legacy" },
  ];
  const groups = groupConversations(threads);
  assert.deepEqual(groups.map(({ path, name }) => [path, name]), [
    ["/labs/api", "api"], ["/other/api", "api"], [null, "Earlier conversations"],
  ]);
  assert.deepEqual(groups[0].threads.map((thread) => thread.root_run_id), ["one", "three"]);
  assert.equal(groups[0].runId, "one");
});
