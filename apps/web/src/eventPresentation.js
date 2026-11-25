import { t } from "./messages.js";

const numbers = new Intl.NumberFormat("en-US");
const seconds = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const offsets = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const eventLabels = new Map(Object.entries(t.events.types));
const itemLabels = new Map(Object.entries(t.events.items));
const lifecycleEvents = new Set(["thread.started", "turn.started", "turn.completed"]);
const statuses = new Map([
  ["in_progress", ["◌", t.status.inProgress]],
  ["completed", ["✓", t.status.completed]],
  ["failed", ["×", t.status.failed]],
  ["declined", ["×", t.status.declined]],
  ["result_unconfirmed", ["?", t.status.actionUnconfirmed]],
]);
const tokenLabels = new Map(Object.entries(t.tokens.fields));
const collaborationLabels = new Map(Object.entries(t.tools.collaboration));

export function formatDuration(ns) {
  if (!Number.isFinite(ns) || ns < 0) return null;
  if (ns < 1_000_000) return "< 1 ms";
  if (ns < 1_000_000_000) return `${Math.floor(ns / 1_000_000)} ms`;
  return `${seconds.format(ns / 1_000_000_000)} s`;
}

export function formatOffset(ns) {
  return Number.isFinite(ns) && ns >= 0 ? `+${offsets.format(ns / 1_000_000_000)} s` : null;
}

export function observedRunDuration(firstNs, lastNs, sinceLastMs = 0) {
  if (!Number.isFinite(firstNs) || !Number.isFinite(lastNs) || firstNs > lastNs) return null;
  return formatDuration(lastNs - firstNs + Math.max(0, sinceLastMs) * 1_000_000);
}

export function displayCommand(raw) {
  if (typeof raw !== "string") return raw;
  const wrapper = /^(?:\/(?:[\w.-]+\/)*)?(?:bash|zsh|sh)[ \t]+-(?:lic|lc|cl|c)[ \t]+([\s\S]+)$/.exec(raw);
  if (!wrapper) return raw;

  const argument = wrapper[1].trimEnd();
  if (/^[\w./-]+$/.test(argument)) return argument;
  const fragments = /'([^']*)'|"((?:[^"\\$`]|\\[\s\S])*)"|\\([^\r\n])/gy;
  let command = "";
  for (let position = 0; position < argument.length;) {
    const fragment = fragments.exec(argument);
    if (!fragment) return raw;
    const doubleQuoted = fragment[2]?.replace(/\\([\\"$`\n])/g, (_, character) => (
      character === "\n" ? "" : character
    ));
    command += fragment[1] ?? doubleQuoted ?? fragment[3];
    position = fragments.lastIndex;
  }
  return command;
}

export function maskPaths(text, workspace = null) {
  if (typeof text !== "string") return text;
  const root = typeof workspace === "string" && workspace.startsWith("/")
    ? workspace.replace(/\/+$/, "") : "";
  const escapedRoot = root.replace(/[.*+?^$\{\}()|[\]\\]/g, "\\$&");
  const masked = root
    ? text.replace(new RegExp("(^|[\\s\"'\x60=:(\\[<{>])" + escapedRoot + "(?=$|[/\\s\"'\x60,;:)\\]}>])", "g"), "$1<folder>")
    : text;
  return masked.replace(/\/(?:home|Users)\/[\w.@+-]+/g, "~");
}

/** Shortens a file path under the conversation's folder, whether or not it was masked for display. */
export function relativePath(path, folder) {
  if (typeof path !== "string" || typeof folder !== "string" || !folder.startsWith("/")) return path;
  const root = folder.replace(/\/+$/, "");
  const prefix = [root, maskPaths(root)].find((candidate) => candidate && path.startsWith(`${candidate}/`));
  return prefix ? path.slice(prefix.length + 1) : path;
}

export function displayValue(value, workspace) {
  if (typeof value === "string") return maskPaths(value, workspace);
  const serialized = JSON.stringify(value, (_, item) => maskPaths(item, workspace), 2);
  return maskPaths(serialized, workspace);
}

export function statusPresentation(status) {
  const statusKey = statuses.has(status) ? status : "result_unconfirmed";
  const [icon, label] = statuses.get(statusKey);
  return { icon, label, statusKey, failed: status === "failed" || status === "declined" };
}

const branchStates = new Map([
  ["running", t.status.inProgress],
  ["completed", t.status.completed],
  ["failed", t.status.failed],
  ["interrupted", t.status.interrupted],
  ["incomplete", t.status.incompleteCapture],
]);

export function branchStateLabel(state) {
  return branchStates.get(state) ?? state;
}

export function firstLine(text) {
  if (typeof text !== "string") return null;
  const [line] = text.trimStart().split(/\r?\n/, 1);
  return line.length > 160 ? `${line.slice(0, 160)}…` : line || null;
}

export function lastNonEmptyLine(text) {
  if (typeof text !== "string") return null;
  const line = text.trimEnd().split(/\r?\n/).at(-1).trim();
  return firstLine(line);
}

export function commandStderr(command) {
  return command?.records.findLast(({ event }) => (
    event.type === "item.completed" && typeof event.item?.stderr === "string" && event.item.stderr.trim()
  ))?.event.item.stderr;
}

export function commandErrorPreview(command, workspace) {
  return lastNonEmptyLine(maskPaths(commandStderr(command) ?? command.output, workspace));
}

export function isOmittedMessage(text) {
  return typeof text === "string" && /^\s*\[redacted:[^\]]+\]\s*$/i.test(text);
}

export function isStartupCommand(command, workspace) {
  if (typeof command !== "string") return false;
  if (command.includes("<global-instructions>")) return true;
  // A relative instruction file can belong to the task workspace.
  const outsideInstruction = /(?:^|[\s"'=])~?\/(?:[^\s"'|;&]*\/)?(?:AGENTS|RTK)\.md(?=$|[\s"'|;&])/;
  return outsideInstruction.test(maskPaths(command, workspace));
}

export function errorMessage(event) {
  const message = event.error?.message ?? event.message;
  return typeof message === "string" && message.trim() ? message : null;
}

export function describeEvent(event, workspace) {
  const { item, type } = event;
  const label = itemLabels.get(item?.type) ?? eventLabels.get(type) ?? t.events.fallback;
  const markdown = item?.type === "agent_message" && typeof item.text === "string"
    ? maskPaths(item.text, workspace) : null;
  const omitted = isOmittedMessage(markdown);
  const failed = type === "turn.failed" || type === "error";
  const text = failed ? errorMessage(event) : item?.message ?? item?.text;
  const tasks = item?.type === "todo_list" && Array.isArray(item.items)
    ? item.items.filter((task) => typeof task?.text === "string")
      .map((task) => ({ ...task, text: maskPaths(task.text, workspace) }))
    : [];
  const technical = !failed && (lifecycleEvents.has(type) || item?.type === "error");
  return { label, markdown: omitted ? null : markdown, omitted,
    text: !markdown && typeof text === "string" ? maskPaths(text, workspace) : null, tasks, failed, technical };
}

function isEmptyMessageStart(entry) {
  const item = entry.event?.item;
  return entry.event?.type === "item.started" && item?.type === "agent_message" && !(typeof item.text === "string" && item.text.trim());
}

export function trajectoryRows(rows, view) {
  if (view === "failures") return rows.filter((entry) => entry.failed);

  const visible = [];
  for (const entry of rows) {
    if (view !== "all" && (entry.startup || isEmptyMessageStart(entry))) continue;
    const title = entry.startup ? t.common.startupGroup : null;
    if (!title && (view === "all" || !entry.technical)) {
      visible.push(entry);
      continue;
    }
    const group = visible.at(-1);
    if (group?.technicalEvents && group.groupTitle === title) group.technicalEvents.push(entry);
    else visible.push({ key: `technical:${entry.key}`, groupTitle: title, technicalEvents: [entry] });
  }
  return visible;
}

export function commandTitle(command) {
  if (typeof command !== "string") return null;
  // Interpret only exact forms with no shell expansion or secondary command.
  const simpleCommand = displayCommand(command);
  const read = /^cat (?:-- )?((?:<folder>\/)?[A-Za-z0-9_./~][A-Za-z0-9_./-]*|"[^"$\x60\\]+"|'[^']+')$/.exec(simpleCommand);
  if (read) {
    const file = read[1].replace(/^(['"])(.*)\1$/, "$2");
    return t.commandTitles.read(file.split("/").filter(Boolean).at(-1) ?? file);
  }
  if (/^ls(?:\s|$)/.test(simpleCommand)) return t.commandTitles.list;
  if (/^find(?:\s|$)/.test(simpleCommand)) return t.commandTitles.find;
  if (/^(?:rg|grep)(?:\s|$)/.test(simpleCommand)) return t.commandTitles.search;
  if (/^sed[ \t]+-n(?:\s|$)/.test(simpleCommand)) return t.commandTitles.excerpt;
  return null;
}

export function reportedTokens(event) {
  if (event.type !== "turn.completed") return [];
  return Array.from(tokenLabels, ([field, label]) => ({ field, label, value: event.usage?.[field] }))
    .filter(({ value }) => Number.isSafeInteger(value) && value >= 0)
    .map(({ field, label, value }) => ({ field, label, amount: value, value: numbers.format(value) }));
}

export function tokenSummary(tokens) {
  const input = tokens.find(({ field }) => field === "input_tokens");
  const output = tokens.find(({ field }) => field === "output_tokens");
  if (input && output && Number.isSafeInteger(input.amount + output.amount)) {
    return { value: numbers.format(input.amount + output.amount), label: t.tokens.total };
  }
  const known = input ?? output;
  return known ? { value: known.value, label: t.tokens.partial(known.label) } : null;
}

export function describeTool({ event }, previous) {
  const { item, type } = event;
  if (!item || !["item.started", "item.updated", "item.completed"].includes(type)) return null;

  let fields;
  switch (item.type) {
    case "mcp_tool_call":
      fields = {
        name: [item.server, item.tool].filter(Boolean).join(" / "),
        input: item.arguments,
        output: item.result,
      };
      break;
    case "web_search":
      fields = {
        name: t.tools.webSearch,
        input: item.action ?? item.query,
        preview: item.query || null,
        output: item.results,
      };
      break;
    case "file_change":
      fields = {
        name: t.tools.fileChanges,
        input: item.changes,
        preview: item.changes?.[0]?.path,
      };
      break;
    case "collab_tool_call":
      fields = {
        name: collaborationLabels.get(item.tool) ?? item.tool ?? t.tools.subagent,
        input: item.prompt,
        output: item.agents_states,
      };
      break;
    default:
      return null;
  }

  const terminal = type === "item.completed";
  if (previous?.terminal && !terminal) return previous;
  const observedStatus = item.status ?? (item.type === "web_search" ? "completed" : "result_unconfirmed");
  const status = terminal ? observedStatus : item.status ?? "in_progress";
  const output = fields.output ?? (previous?.terminal ? null : previous?.output);
  const error = item.error?.message ?? (previous?.terminal ? null : previous?.error);
  const conflictingResults = previous?.conflictingResults || (previous?.terminal && (
    status !== previous.status || error !== previous.error
    || (output != null && previous.output != null && JSON.stringify(output) !== JSON.stringify(previous.output))
  ));
  return {
    name: fields.name || previous?.name || t.tools.mcp,
    input: fields.input ?? previous?.input,
    output,
    error: error ?? null,
    preview: fields.preview ?? previous?.preview,
    terminal,
    status,
    conflictingResults: Boolean(conflictingResults),
  };
}

export function isEssential(entry) {
  return !entry.startup && (entry.branch || entry.command || entry.tool || entry.failed
    || ["agent_message", "reasoning"].includes(entry.event?.item?.type));
}

export function visibleTrajectoryRows(rows, view) {
  return trajectoryRows(rows, view).filter((entry) => view !== "essential" || isEssential(entry));
}
