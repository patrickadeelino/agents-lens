import assert from "node:assert/strict";
import { test } from "node:test";
import { createRunTracker } from "../src/runTracker.js";

const settle = () => new Promise((resolve) => setImmediate(resolve));

function tracker({ watch, isActive = async () => false }) {
  const states = [];
  const value = createRunTracker({ watch, isActive, onChange: (runId) => states.push(runId), wait: settle });
  return { value, states };
}

test("the active run is held until its stream ends", async () => {
  let end;
  let watched = 0;
  const { value, states } = tracker({ watch: () => new Promise((resolve) => { watched += 1; end = resolve; }) });
  value.track("first");
  value.track("first");
  await settle();
  assert.deepEqual(states, ["first"]);
  assert.equal(watched, 1);
  end();
  await settle();
  assert.deepEqual(states, ["first", null]);
});

test("a lost connection keeps the run until the server reports it finished", async () => {
  const answers = [true, false];
  let watched = 0;
  const { value, states } = tracker({
    watch: async () => { watched += 1; throw new Error("interrupted"); },
    isActive: async () => answers.shift(),
  });
  value.track("first");
  for (let turn = 0; turn < 5; turn += 1) await settle();
  assert.equal(watched, 2);
  assert.deepEqual(states, ["first", null]);
});

test("an unanswered status check does not release the run", async () => {
  let watched = 0;
  const { value, states } = tracker({
    watch: () => ++watched === 1 ? Promise.reject(new Error("interrupted")) : new Promise(() => {}),
    isActive: async () => { throw new Error("offline"); },
  });
  value.track("first");
  for (let turn = 0; turn < 5; turn += 1) await settle();
  assert.deepEqual(states, ["first"]);
  value.stop();
});
