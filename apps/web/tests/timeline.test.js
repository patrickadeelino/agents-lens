import assert from "node:assert/strict";
import { test } from "node:test";
import { CommandTimeline } from "../src/timeline.js";

function commandRecord(elapsedNs, type, id, fields) {
  return {
    elapsed_ns: elapsedNs,
    event: { type, item: { id, type: "command_execution", ...fields } },
  };
}

test("a command that fails before an earlier command keeps its original row", () => {
  const timeline = new CommandTimeline();
  timeline.observe(commandRecord(100_000_000, "item.started", "a", {
    command: "cat README.md",
    status: "in_progress",
  }));
  timeline.observe(commandRecord(200_000_000, "item.started", "b", {
    command: "cat compose.yaml",
    status: "in_progress",
  }));

  assert.deepEqual(timeline.rows.map(({ id, status }) => [id, status]), [
    ["a", "in_progress"],
    ["b", "in_progress"],
  ]);

  timeline.observe(commandRecord(350_000_000, "item.completed", "b", {
    status: "failed",
    exit_code: 1,
    aggregated_output: "cat: compose.yaml: No such file or directory",
  }));

  const afterFailure = timeline.rows;
  assert.deepEqual(afterFailure.map(({ id, status }) => [id, status]), [
    ["a", "in_progress"],
    ["b", "failed"],
  ]);
  assert.equal(afterFailure[0].observedDurationNs, null);
  assert.equal(afterFailure[1].command, "cat compose.yaml");
  assert.equal(afterFailure[1].output, "cat: compose.yaml: No such file or directory");
  assert.equal(afterFailure[1].exitCode, 1);
  assert.equal(afterFailure[1].startedOffsetNs, 200_000_000);
  assert.equal(afterFailure[1].completedOffsetNs, 350_000_000);
  assert.equal(afterFailure[1].observedDurationNs, 150_000_000);
  assert.deepEqual(afterFailure[1].records.map(({ event }) => event.type), [
    "item.started",
    "item.completed",
  ]);

  timeline.observe(commandRecord(600_000_000, "item.completed", "a", {
    status: "completed",
    exit_code: 0,
    aggregated_output: "Juniper",
  }));

  assert.deepEqual(timeline.rows.map(({ id, status, observedDurationNs }) => [id, status, observedDurationNs]), [
    ["a", "completed", 500_000_000],
    ["b", "failed", 150_000_000],
  ]);
});

test("a history completion uses a reported duration without assuming a start", () => {
  const timeline = new CommandTimeline();
  timeline.observe(commandRecord(400_000_000, "item.completed", "a", {
    status: "completed",
    exit_code: 0,
  }), 250_000_000);
  timeline.observe(commandRecord(500_000_000, "item.completed", "b", {
    status: "failed",
    exit_code: 1,
  }));

  assert.deepEqual(timeline.rows.map(({ id, startMissing, observedDurationNs }) => (
    [id, startMissing, observedDurationNs]
  )), [
    ["a", true, 250_000_000],
    ["b", true, null],
  ]);
});

test("ending a capture marks the unfinished rows and leaves completed rows intact", () => {
  const timeline = new CommandTimeline();
  timeline.observe(commandRecord(100_000_000, "item.started", "a", { status: "in_progress" }));
  timeline.observe(commandRecord(200_000_000, "item.completed", "b", { status: "completed" }));

  assert.deepEqual(timeline.finish().map(({ id, status }) => [id, status]), [["a", "result_unconfirmed"]]);
  assert.deepEqual(timeline.rows.map(({ id, status }) => [id, status]), [
    ["a", "result_unconfirmed"],
    ["b", "completed"],
  ]);
});

test("conflicting completions keep both results visible for inspection", () => {
  const timeline = new CommandTimeline();
  timeline.observe(commandRecord(100_000_000, "item.completed", "a", { status: "completed", exit_code: 0 }));
  timeline.observe(commandRecord(200_000_000, "item.completed", "a", { status: "failed", exit_code: 1 }));

  const [row] = timeline.rows;
  assert.equal(row.conflictingResults, true);
  assert.deepEqual(row.records.map(({ event }) => event.item.exit_code), [0, 1]);
});
