import assert from "node:assert/strict";
import { test } from "node:test";
import { commandTitle, describeEvent, trajectoryRows } from "../src/eventPresentation.js";

test("Essential folds only adjacent technical events and leaves failures inspectable", () => {
  const rows = [
    { type: "thread.started" },
    { type: "item.completed", item: { type: "error", message: "CLI warning" } },
    { type: "item.completed", item: { type: "agent_message", text: "Vou ler o README." } },
    { type: "turn.completed" },
    { type: "error", message: "Fatal stream error" },
    { type: "turn.failed", error: { message: "Turn failed" } },
  ].map((event, key) => ({ key, ...describeEvent(event) }));

  const essential = trajectoryRows(rows, "essential");
  assert.deepEqual(essential.map((row) => row.technicalEvents?.map(({ key }) => key) ?? row.key), [
    [0, 1], 2, [3], 4, 5,
  ]);
  assert.deepEqual(trajectoryRows(rows, "failures"), rows.slice(4));
  assert.deepEqual(trajectoryRows(rows, "all"), rows);
});

test("plain action titles preserve ambiguity for complex shell commands", () => {
  assert.equal(commandTitle("cat README.md"), "Ler README.md");
  assert.equal(commandTitle("zsh -lc 'cat examples/README.md'"), "Ler README.md");
  assert.equal(commandTitle("ls -a"), "Listar arquivos, incluindo ocultos");
  assert.equal(commandTitle("cat README.md | grep setup"), null);
  assert.equal(commandTitle("cat $README"), null);
  assert.equal(commandTitle("cat -n README.md"), null);
});
