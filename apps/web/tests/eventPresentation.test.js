import assert from "node:assert/strict";
import { test } from "node:test";
import { commandTitle, describeEvent, displayCommand, displayValue, formatDuration, formatOffset, maskPaths, observedRunDuration, trajectoryRows } from "../src/eventPresentation.js";

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

test("shell display preserves literal quotes and unwraps only a single literal argument", () => {
  const raw = "printf '%s' \"abc\"";
  assert.equal(displayCommand(raw), raw);
  assert.equal(displayCommand('/usr/bin/zsh -lc "cat \\"a b\\""'), 'cat "a b"');
  assert.equal(displayCommand("bash -lc 'printf '\\''%s'\\'' abc'"), "printf '%s' abc");
  assert.equal(displayCommand("sh -c 'printf \"%s\" \"$HOME\"'"), 'printf "%s" "$HOME"');
  assert.equal(displayCommand("/opt/bin/zsh -lic 'rg \"Agent Lens\" README.md'"), 'rg "Agent Lens" README.md');
  assert.equal(displayCommand('bash -lc "echo $HOME"'), 'bash -lc "echo $HOME"');
  assert.equal(displayCommand("sh -c 'echo hi' alternate"), "sh -c 'echo hi' alternate");
});

test("path masking is limited to display and prefers a known workspace over the home alias", () => {
  const workspace = "/home/example/my project";
  const record = { path: workspace, command: `cat "${workspace}/README.md"`, home: "/Users/other/.codex/AGENTS.md" };
  assert.equal(maskPaths(record.command, workspace), 'cat "<workspace>/README.md"');
  assert.equal(maskPaths("/home/example/my project-backup", workspace), "~/my project-backup");
  assert.deepEqual(JSON.parse(displayValue(record, workspace)), {
    path: "<workspace>", command: 'cat "<workspace>/README.md"', home: "~/.codex/AGENTS.md",
  });
  assert.equal(record.path, workspace);
  assert.equal(maskPaths("cd /var/scratch", "/var/scratch/"), "cd <workspace>");
});

test("duration and relative start use the units in the approved screens", () => {
  assert.equal(formatDuration(700_000), "< 1 ms");
  assert.equal(formatDuration(200_900_000), "200 ms");
  assert.equal(formatDuration(1_200_000_000), "1,2 s");
  assert.equal(formatOffset(200_000_000), "+0,20 s");
  assert.equal(formatDuration(undefined), null);
});

test("run duration uses capture offsets and can add time since the live arrival", () => {
  assert.equal(observedRunDuration(8_000_000_000, 9_200_000_000), "1,2 s");
  assert.equal(observedRunDuration(8_000_000_000, 9_200_000_000, 800), "2,0 s");
  assert.equal(observedRunDuration(null, 9_200_000_000), null);
});

test("plain action titles preserve ambiguity for complex shell commands", () => {
  assert.equal(commandTitle("cat README.md"), "Ler README.md");
  assert.equal(commandTitle("zsh -lc 'cat examples/README.md'"), "Ler README.md");
  assert.equal(commandTitle("ls -a"), "Listar arquivos, incluindo ocultos");
  assert.equal(commandTitle("cat README.md | grep setup"), null);
  assert.equal(commandTitle("cat $README"), null);
  assert.equal(commandTitle("cat -n README.md"), null);
});
