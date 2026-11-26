import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";
import { defineComponent, h, provide, ref } from "vue";
import { renderToString } from "vue/server-renderer";

test("a conversation names its own agent in messages and replies", async () => {
  const vite = await createServer({ appType: "custom", server: { middlewareMode: true, watch: null, ws: false } });
  try {
    const { default: EventRow } = await vite.ssrLoadModule("/src/EventRow.vue");
    const { default: ConversationTurn } = await vite.ssrLoadModule("/src/ConversationTurn.vue");
    const within = (agent, component, props) => renderToString(h(defineComponent(() => {
      provide("agent", ref(agent));
      return () => h(component, props);
    })));
    const entry = { markdown: "Done.", event: { item: { type: "agent_message" } } };
    const turn = { run_id: "r", prompt: "p", assistant_markdown: "Done.", status: "completed" };

    const message = await within("claude_code", EventRow, { entry });
    assert.match(message, /Claude Code/);
    assert.doesNotMatch(message, /Codex/);
    assert.match(await within("claude_code", ConversationTurn, { turn, number: 1 }), /Claude Code reply/);
    assert.match(await within("codex", ConversationTurn, { turn, number: 1 }), /Codex reply/);
  } finally {
    await vite.close();
  }
});
