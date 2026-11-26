import { strict as assert } from "node:assert";
import { test } from "node:test";
import { agentName, chooseAgent, loadAgents } from "./agents.js";
import { tokenSummary } from "./eventPresentation.js";

test("an unknown agent is never named after another one", () => {
  assert.deepEqual(["codex", "claude_code", "other", undefined].map(agentName), ["Codex", "Claude Code", "Agent", "Agent"]);
});

test("the choice is kept while it can run", () => {
  assert.equal(chooseAgent("claude_code", ["codex", "claude_code"]), "claude_code");
  assert.equal(chooseAgent("codex", ["claude_code"]), "claude_code");
  assert.equal(chooseAgent("codex", []), "codex");
});

test("only known agents are listed, and a failed request lists none", async () => {
  const answer = (ok, body) => async () => ({ ok, json: async () => body });
  assert.deepEqual(await loadAgents(answer(true, { agents: ["codex", "new_agent", "claude_code"] })), ["codex", "claude_code"]);
  assert.deepEqual(await loadAgents(answer(true, {})), []);
  assert.deepEqual(await loadAgents(answer(false, { agents: ["codex"] })), []);
  assert.deepEqual(await loadAgents(async () => { throw new Error("offline"); }), []);
});

test("the token total names the agent that reported it", () => {
  const tokens = [{ field: "input_tokens", amount: 2 }, { field: "output_tokens", amount: 3 }];
  assert.equal(tokenSummary(tokens, "Claude Code").label, "tokens · reported by Claude Code");
});
