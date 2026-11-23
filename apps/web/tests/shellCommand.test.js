import assert from "node:assert/strict";
import { test } from "node:test";
import { formatShellCommand } from "../src/shellCommand.js";

test("long find commands keep option values and escaped groups together", () => {
  const raw = "find . -maxdepth 2 \\( -iname 'README*' -o -iname 'readme*' \\) -type f";
  assert.equal(formatShellCommand(raw), [
    "find .", "    -maxdepth 2", "    \\( -iname 'README*' -o -iname 'readme*' \\)", "    -type f",
  ].join("\n"));
});

test("chains break after each top-level operator without indenting the next stage", () => {
  const raw = "pwd && rg --files -g 'README*' && sed -n '1,160p' README.md";
  assert.equal(formatShellCommand(raw), "pwd &&\nrg --files -g 'README*' &&\nsed -n '1,160p' README.md");
  assert.equal(formatShellCommand("first||second; third"), "first||\nsecond;\nthird");
});

test("pipes stay at the end of the line", () => {
  assert.equal(formatShellCommand("cat README.md | rg -n 'setup' | head -n 5"), "cat README.md |\nrg -n 'setup' |\nhead -n 5");
});

test("quoted and escaped operators keep every literal character", () => {
  const raw = String.raw`printf '%s' "Agent  &&  Lens \"quoted\"" \| \;`;
  assert.equal(formatShellCommand(raw), raw);
  assert.equal(formatShellCommand("printf 'x &&  y' && pwd"), "printf 'x &&  y' &&\npwd");
});

test("returns the original for an unbalanced quote, an incomplete escape or unsupported shell syntax", () => {
  for (const raw of ["pwd && rg 'README", 'pwd && rg "README', "pwd && printf hello\\", 'echo "$(pwd && ls)" && pwd']) {
    assert.equal(formatShellCommand(raw), raw);
  }
});

test("short commands stay on a single line", () => {
  assert.equal(formatShellCommand("ls  -a"), "ls  -a");
  const exactly64 = "cat " + "a".repeat(57) + " -n";
  assert.equal(formatShellCommand(exactly64), exactly64);
});
