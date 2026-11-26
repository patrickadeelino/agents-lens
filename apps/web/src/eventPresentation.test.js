import { strict as assert } from "node:assert";
import { test } from "node:test";
import { describeTool } from "./eventPresentation.js";

const item = { id: "t1", type: "tool_call", tool: "Read", arguments: { file_path: "/repo/calc.py" } };

test("a generic tool call keeps its name, input and outcome", () => {
  const started = describeTool({ event: { type: "item.started", item: { ...item, status: "in_progress" } } });
  assert.deepEqual([started.name, started.preview, started.status], ["Read", "/repo/calc.py", "in_progress"]);

  const failed = { ...item, status: "failed", error: { message: "No such file" } };
  const completed = describeTool({ event: { type: "item.completed", item: failed } }, started);
  assert.deepEqual(
    [completed.name, completed.input, completed.status, completed.error, completed.terminal],
    ["Read", item.arguments, "failed", "No such file", true],
  );
});
