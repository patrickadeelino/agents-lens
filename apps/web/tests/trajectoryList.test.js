import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";

test("a subagent shows its counts only after its first action", async () => {
  const vite = await createServer({ appType: "custom", server: { middlewareMode: true, watch: null, ws: false } });
  try {
    const { default: TrajectoryList } = await vite.ssrLoadModule("/src/TrajectoryList.vue");
    const branch = { key: "child", branch: true, label: "Subagent 1", state: "running", rows: [], terminal: false };
    const render = (fields) => renderToString(h(TrajectoryList, {
      rows: [{ ...branch, ...fields }], filter: "essential", isOpen: () => false,
    }));
    assert.doesNotMatch(await render({ actionCount: 0, duration: "< 1 ms" }), /delegation-meta|&lt; 1 ms/);
    assert.match(await render({ actionCount: 1, duration: "2.5 s" }), /delegation-meta">1 action.*2\.5 s/);
  } finally {
    await vite.close();
  }
});
