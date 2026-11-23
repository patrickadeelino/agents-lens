import assert from "node:assert/strict";
import { test } from "node:test";
import { renderAgentMarkdown } from "../src/markdown.js";

test("agent messages render Markdown without activating embedded HTML or unsafe URLs", () => {
  const rendered = renderAgentMarkdown([
    "**serviço**",
    "[documentação](https://example.com/docs)",
    '<img src="https://example.com/a" onerror="alert(1)">',
    "![imagem externa](https://example.com/b)",
    "[link inseguro](javascript:alert%281%29)",
  ].join("\n\n"));

  assert.match(rendered, /<strong>serviço<\/strong>/);
  assert.match(rendered, /<a href="https:\/\/example.com\/docs">documentação<\/a>/);
  assert.match(rendered, /&lt;img/);
  assert.match(rendered, /imagem externa/);
  assert.doesNotMatch(rendered, /<img|href="javascript:/i);
});
