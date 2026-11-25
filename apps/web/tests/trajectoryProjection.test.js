import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { visibleTrajectoryRows } from "../src/eventPresentation.js";
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

test("projects the real sanitized delegation without adding child actions to parent totals", () => {
  const source = new URL("./fixtures/catalog-delegation.timed.sanitized.jsonl", import.meta.url);
  const projection = new TrajectoryProjection();
  const events = readFileSync(source, "utf8").trim().split("\n").map(JSON.parse);
  events.forEach((value, index) => projection.append(value, index + 1));
  const view = projection.snapshot();
  assert.equal(view.actions.length, 1);
  const child = view.rows.find((entry) => entry.branch);
  assert.equal(child.state, "completed");
  const read = child.rows.find((entry) => entry.command?.command.includes("cat catalog.py"));
  assert.match(read.command.output, /class CatalogHandler/);
  assert.match(actionCallId(read), /^child:/);
  assert.equal(actionCallId(view.actions[0]), view.actions[0].event.item.id);
});

test("joins early child activity after paging in its edge and keeps the failed path", () => {
  const edge = (parent, child, turn) => ({ type: "delegation.edge",
    parent_thread_id: parent, child_thread_id: child, parent_turn_id: turn });
  const childCommand = (thread, turn, status) => ({ type: "delegation.event", thread_id: thread, turn_id: turn,
    observation: command("same", status, 0, status === "failed" ? 1 : 0).event });
  const projection = new TrajectoryProjection();
  projection.prepend([
    delivery(3, record(edge("child", "nested", "turn"), 3)),
    delivery(4, record(childCommand("child", "turn", "completed"), 4)),
    delivery(5, record(childCommand("nested", "turn", "failed"), 5)),
    delivery(6, record({ type: "delegation.incomplete", thread_id: "nested" }, 6)),
  ], {}, "root");
  assert.equal(projection.snapshot().rows.length, 0);
  projection.prepend([
    delivery(1, record(childCommand("child", "earlier-turn", "completed"), 1)),
    delivery(2, record(edge("root", "child", "main"), 2)),
  ]);
  const [child] = visibleTrajectoryRows(projection.snapshot().rows, "failures");
  const [nested] = visibleTrajectoryRows(child.rows, "failures");
  assert.equal(nested.state, "incomplete");
  assert.equal(nested.rows[0].failed, true);
  const links = child.rows.filter((row) => row.command).map(actionCallId);
  assert.equal(new Set([...links, actionCallId(nested.rows[0])]).size, 3);
  assert.equal(projection.snapshot().failures.length, 0);
});

test("delegation numbers stay stable across pages and label only observed wait targets", () => {
  const projection = new TrajectoryProjection();
  const edge = (thread, child_index) => ({ type: "delegation.edge", parent_thread_id: "root",
    child_thread_id: thread, child_index });
  const wait = (id, targets) => ({ type: "item.completed", item: {
    type: "collab_tool_call", id, tool: "wait", status: "completed", receiver_thread_ids: targets,
  } });
  projection.prepend([
    delivery(3, record(edge("tests", 2), 3)),
    delivery(4, record(wait("both", ["tests", "api"]), 4)),
    delivery(5, record(wait("unknown", []), 5)),
    delivery(6, record(wait("state", []), 6)),
    delivery(7, record({ type: "delegation.event", thread_id: "tests", source_thread_id: "root",
      turn_id: "turn", item_id: "state", observation: { type: "subagent.wait" } }, 7)),
    delivery(8, record({ type: "delegation.event", thread_id: "tests",
      observation: { type: "subagent.completed", item: { agentPath: "/root/testes" } } }, 8)),
  ], {}, "root");
  assert.equal(projection.snapshot().rows[0].label, "Subagente 2 · testes");
  projection.prepend([
    delivery(1, record({ type: "thread.started", thread_id: "root" }, 1)),
    delivery(2, record(edge("api", 1), 2)),
  ]);
  assert.deepEqual(projection.snapshot().rows.filter((row) => row.branch).map((row) => row.label), [
    "Subagente 1", "Subagente 2 · testes",
  ]);
  assert.deepEqual(projection.snapshot().actions.map((row) => row.tool.name), [
    "Aguardar subagentes 1 e 2 · testes", "Aguardar atualização de subagente", "Aguardar subagente 2 · testes",
  ]);
});
