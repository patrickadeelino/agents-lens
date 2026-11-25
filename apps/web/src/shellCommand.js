const stepLimit = 64;
const continuation = "\n    ";

function tokenizeShellCommand(raw) {
  const lexeme = /\s+|(&&|\|\||[|;])|(?:\\[^\r\n]|'[^']*'|"(?:\\[^\r\n]|[^"\\\x60$]|\$(?!\())*"|<folder>|<global-instructions>|[^\s'"\\&|;(){}<>\x60])+/gy;
  const tokens = [];
  let depth = 0;
  for (let position = 0; position < raw.length; position = lexeme.lastIndex) {
    const match = lexeme.exec(raw);
    if (!match) return null;
    const [text, operator] = match;
    if (!text.trim()) continue;
    if (text.startsWith("#")) return null;
    const token = { text, operator, depth, start: position, end: lexeme.lastIndex };
    tokens.push(token);
    if (text === "\\(") depth++;
    if (text === "\\)") depth--;
    if (depth < 0) return null;
  }
  return depth === 0 ? tokens : null;
}

function wrapOptions(tokens, start, end, breaks) {
  if (start === end || tokens[end - 1].end - tokens[start].start <= stepLimit) return;
  for (let index = start + 1; index < end; index++) {
    const token = tokens[index];
    if (token.depth === 0 && (token.text.startsWith("-") || token.text === "\\(")) {
      breaks.push({ start: tokens[index - 1].end, end: token.start, separator: continuation });
    }
  }
}

export function formatShellCommand(raw) {
  if (typeof raw !== "string" || /[\r\n]/.test(raw)) return raw;
  const tokens = tokenizeShellCommand(raw);
  if (!tokens?.length) return raw;

  const breaks = [];
  let stepStart = 0;
  for (const [index, token] of tokens.entries()) {
    if (!token.operator || token.depth !== 0) continue;
    if (index === stepStart || (index === tokens.length - 1 && token.operator !== ";")) return raw;
    wrapOptions(tokens, stepStart, index, breaks);
    if (tokens[index + 1]) {
      breaks.push({ start: token.end, end: tokens[index + 1].start, separator: "\n" });
    }
    stepStart = index + 1;
  }
  wrapOptions(tokens, stepStart, tokens.length, breaks);

  let cursor = 0;
  const formatted = [];
  for (const { start, end, separator } of breaks) {
    formatted.push(raw.slice(cursor, start), separator);
    cursor = end;
  }
  return formatted.join("") + raw.slice(cursor);
}

export function shellCommandParts(text) {
  const tokens = tokenizeShellCommand(text);
  if (!tokens) return [{ text }];
  const parts = [];
  let cursor = 0;
  let atExecutable = true;
  for (const token of tokens) {
    if (token.start > cursor) parts.push({ text: text.slice(cursor, token.start) });
    let kind;
    if (token.operator && token.depth === 0) {
      atExecutable = true;
    } else if (atExecutable && !/^[a-z_]\w*=/i.test(token.text)) {
      kind = "executable";
      atExecutable = false;
    } else if (token.text.startsWith("-")) {
      kind = "flag";
    }
    parts.push({ text: token.text, token: true, kind });
    cursor = token.end;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor) });
  return parts;
}
