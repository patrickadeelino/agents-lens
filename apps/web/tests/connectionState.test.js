import assert from "node:assert/strict";
import test from "node:test";
import { CONNECTION, connectionLabel, isDisrupted } from "../src/connectionState.js";
import { branchStateLabel, statusPresentation } from "../src/eventPresentation.js";

test("connection identifiers are independent of their visible text", () => {
  assert.deepEqual(Object.keys(CONNECTION).sort(), ["ended", "idle", "interrupted", "loading", "receiving", "reconnecting"]);
  for (const state of Object.values(CONNECTION)) assert.notEqual(connectionLabel(state), state);
  assert.equal(isDisrupted(CONNECTION.reconnecting), true);
  assert.equal(isDisrupted(CONNECTION.interrupted), true);
  assert.equal(isDisrupted(CONNECTION.receiving), false);
});

test("an unknown connection state is shown as received", () => {
  assert.equal(connectionLabel("something-else"), "something-else");
});

test("action status keys survive a change of label", () => {
  assert.equal(statusPresentation("completed").statusKey, "completed");
  assert.equal(statusPresentation("failed").failed, true);
  assert.equal(statusPresentation("in_progress").statusKey, "in_progress");
  assert.equal(statusPresentation("something-new").statusKey, "result_unconfirmed");
});

test("delegation branch states have a label for every identifier", () => {
  for (const state of ["running", "completed", "failed", "interrupted", "incomplete"]) {
    assert.notEqual(branchStateLabel(state), state);
  }
});
