import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";

test("the rendered command keeps the raw straight quotes", async () => {
  const vite = await createServer({
    appType: "custom",
    server: { middlewareMode: true, watch: null, ws: false },
  });
  try {
    const { default: ActionRow } = await vite.ssrLoadModule("/src/ActionRow.vue");
    const command = `printf '%s' "Agent Lens"`;
    const markup = await renderToString(h(ActionRow, { entry: { command: { command } } }));
    const rendered = /<code[^>]*>([^<]*)<\/code>/.exec(markup)?.[1]
      .replaceAll("&#39;", "'").replaceAll("&quot;", '"');
    assert.equal(rendered, command);
  } finally {
    await vite.close();
  }
});
