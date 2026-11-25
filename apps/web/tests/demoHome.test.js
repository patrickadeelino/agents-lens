import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";
import { h } from "vue";
import { renderToString } from "vue/server-renderer";

test("the demo home links each recorded conversation instead of offering a prompt", async () => {
  const vite = await createServer({ appType: "custom", server: { middlewareMode: true, watch: null, ws: false } });
  try {
    const { default: DemoHome } = await vite.ssrLoadModule("/src/DemoHome.vue");
    const threads = [{ root_run_id: "one", thread_id: "abc", title: "Add a filter", preview: "Done.", workspace: "/workspace/catalog-api" },
      { root_run_id: "two", thread_id: "def", title: "Same text", preview: "Same text" }];
    const markup = await renderToString(h(DemoHome, { threads }));
    assert.match(markup, /href="\?thread_id=abc"/);
    assert.match(markup, /Add a filter/);
    assert.match(markup, /catalog-api/);
    assert.doesNotMatch(markup, /<textarea|<form/);
    assert.equal(markup.match(/Same text/g).length, 1);
  } finally {
    await vite.close();
  }
});
