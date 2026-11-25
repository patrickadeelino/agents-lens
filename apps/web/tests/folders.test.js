import assert from "node:assert/strict";
import { test } from "node:test";
import { childPath, folderTitle, listFolders, parentPath, selectFolder } from "../src/folders.js";

test("folder paths stay relative to the workspace root", () => {
  assert.equal(childPath("", "apps"), "apps");
  assert.equal(childPath("apps", "web"), "apps/web");
  assert.equal(parentPath("apps/web"), "apps");
  assert.equal(parentPath("apps"), "");
  assert.equal(folderTitle("/home/demo/work", ""), "/home/demo/work");
  assert.equal(folderTitle("/home/demo/work", "apps"), "/home/demo/work/apps");
  assert.equal(folderTitle("/", "apps"), "/apps");
});

test("folder requests encode the path and mark the selection as intentional", async (context) => {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push([url, options]);
    return Response.json({ ok: true });
  });
  await listFolders("a b/c&d");
  await selectFolder("a b");
  assert.equal(calls[0][0], "/folders?path=a+b%2Fc%26d");
  assert.equal(calls[1][1].headers["X-Agent-Lens"], "folder-selection");
  assert.equal(calls[1][1].body, '{"path":"a b"}');
});

test("folder failures show the server's reason, or a readable fallback", async (context) => {
  const responses = [
    Response.json({ detail: "Folder is unavailable." }, { status: 409 }),
    new Response("Bad Gateway", { status: 502 }),
  ];
  context.mock.method(globalThis, "fetch", async () => responses.shift() ?? Promise.reject(new TypeError("offline")));
  await assert.rejects(listFolders(""), { message: "Folder is unavailable.", status: 409 });
  await assert.rejects(listFolders(""), { message: "Could not load folders.", status: 502 });
  await assert.rejects(selectFolder(""), { message: "Could not select this folder." });
});
