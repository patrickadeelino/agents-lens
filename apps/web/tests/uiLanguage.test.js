// Guards the en-US interface: no Portuguese in shipped sources or fixtures, and
// no visible text written in a template instead of src/messages.js.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { parse } from "vue/compiler-sfc";

const root = new URL("../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");
const list = (directory) => readdirSync(new URL(directory, root)).map((name) => `${directory}${name}`);
const sources = list("src/").filter((path) => /\.(js|vue|css)$/.test(path));

const accented = /[áàâãéêíóôõúç]/i;
// Common interface words that carry no accent and are not English.
const words = /\b(abrir|aguardando|arquivos?|buscar|carregando|carregar|comando|conversas?|copiar|detalhes|encerrado|enviar|entrada|evento|executar|falhas?|falhou|fechar|inspecionar|mostrar|nenhuma?|nova|pasta|recolher|resposta|selecionar|tentar|turnos?|tudo|vivo|vou)\b/i;
const visibleAttributes = new Set(["alt", "aria-label", "label", "placeholder", "title"]);
const textNode = 2;
const attributeNode = 6;
const hasLetters = /\p{L}/u;

function literalTemplateText(node, found = []) {
  if (node.type === textNode && hasLetters.test(node.content)) found.push(node.content.trim());
  for (const prop of node.props ?? []) {
    if (prop.type === attributeNode && visibleAttributes.has(prop.name) && hasLetters.test(prop.value?.content ?? "")) {
      found.push(`${prop.name}="${prop.value.content}"`);
    }
  }
  for (const child of node.children ?? []) {
    if (typeof child === "object") literalTemplateText(child, found);
  }
  return found;
}

test("shipped sources and fixtures contain no Portuguese", () => {
  const found = ["index.html", ...sources, ...list("tests/fixtures/")].flatMap((path) => (
    read(path).split("\n").flatMap((line, index) => (
      accented.test(line) || words.test(line) ? [`${path}:${index + 1}: ${line.trim().slice(0, 120)}`] : []
    ))
  ));
  assert.deepEqual(found, []);
});

test("component templates take their visible text from the message catalog", () => {
  const found = sources.filter((path) => path.endsWith(".vue")).flatMap((path) => {
    const { descriptor, errors } = parse(read(path));
    assert.deepEqual(errors, [], path);
    return literalTemplateText(descriptor.template.ast).map((text) => `${path}: ${text}`);
  });
  assert.deepEqual(found, []);
});

test("the document declares English", () => {
  assert.match(read("index.html"), /<html lang="en">/);
});
