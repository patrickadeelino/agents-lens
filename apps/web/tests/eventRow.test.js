import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";

test("each narrative row renders one body, and the final reply starts collapsed", async () => {
  const vite = await createServer({ appType: "custom", server: { middlewareMode: true, watch: null, ws: false } });
  try {
    const { default: EventRow } = await vite.ssrLoadModule("/src/EventRow.vue");
    const message = { markdown: "The tests pass.", event: { item: { type: "agent_message" } } };
    const reasoning = { text: "Checking the handler.", event: { item: { type: "reasoning" } } };
    const render = (props) => renderToString(h(EventRow, props));

    const ordinary = await render({ entry: message });
    assert.match(ordinary, /The tests pass\./);
    assert.doesNotMatch(ordinary, /Final reply/);

    const final = await render({ entry: message, final: true });
    assert.match(final, /Final reply/);
    assert.match(final, /Show here/);
    assert.doesNotMatch(final, /The tests pass\./);

    const thought = await render({ entry: reasoning });
    assert.match(thought, /Checking the handler\./);
    assert.equal(thought.match(/class="run-kicker"/g).length, 1);
  } finally {
    await vite.close();
  }
});
