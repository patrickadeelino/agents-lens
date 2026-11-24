import assert from "node:assert/strict";
import test from "node:test";
import { actionCallId, observedFileChanges, runOutcome, savedOutcome, TrajectoryProjection } from "../src/trajectoryProjection.js";

const record = (event, ms) => ({ event, elapsed_ns: ms * 1_000_000 });
const command = (id, status, ms, exitCode) => record({
  type: status === "in_progress" ? "item.started" : "item.completed",
  item: { type: "command_execution", id, command: `run ${id}`, status, exit_code: exitCode },
}, ms);
const delivery = (event_id, record) => ({ event_id, record });
const files = (status, changes) => record({
  type: status === "in_progress" ? "item.started" : "item.completed",
  item: { id: "edit", type: "file_change", status, changes },
}, 10);

test("recovered and live observations share ordering, deduplication, and terminal item state", () => {
  const projection = new TrajectoryProjection();
  projection.prepend([
    delivery(2, command("unfinished", "in_progress", 20)),
    delivery(3, command("tests", "failed", 30, 1)),
  ]);
  assert.equal(projection.append(command("tests", "failed", 30, 1), "3"), false);
  assert.equal(projection.append(command("check", "completed", 40, 0), "4"), true);
  projection.finish();
  projection.prepend([
    delivery(1, command("tests", "in_progress", 10)),
    delivery(2, command("unfinished", "in_progress", 20)),
  ]);

  const data = projection.snapshot();
  assert.deepEqual(data.actions.map(actionCallId), ["tests", "unfinished", "check"]);
  assert.deepEqual(data.actions.map((entry) => entry.command.status), ["failed", "result_unconfirmed", "completed"]);
  assert.equal(data.failures.length, 1);
  assert.equal(data.firstOffset, 10_000_000);
  assert.equal(data.lastOffset, 40_000_000);
  assert.equal(data.terminal, true);
});

test("a saved active capture does not finalize pending tool results", () => {
  assert.deepEqual(savedOutcome({ status: "unconfirmed", continuation: { state: "active" } }), {
    status: "active", detail: null, terminal: false,
  });
  assert.equal(runOutcome({ exit_code: 0 }, "turn.completed").status, "completed");
  assert.equal(runOutcome({ error: "capture_unavailable" }, "turn.completed").terminal, false);
});

test("file highlights require observed successful changes and preserve their source action", () => {
  const projection = new TrajectoryProjection();
  const changes = [{ path: "src/catalog.py", kind: "update" }, null, { kind: "update" }];
  projection.append(files("in_progress", changes), 1);
  assert.deepEqual(observedFileChanges(projection.snapshot().actions), []);
  projection.append(files("completed", changes), 2);
  const { actions } = projection.snapshot();
  const [file] = observedFileChanges(actions);
  assert.equal(file.path, "src/catalog.py");
  assert.equal(actionCallId(file.entry), "edit");
  projection.append(files("failed", changes), 3);
  assert.deepEqual(observedFileChanges(projection.snapshot().actions), []);
});
