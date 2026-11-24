import {
  commandErrorPreview, commandTitle, describeEvent, describeTool, displayCommand, errorMessage,
  formatDuration, formatOffset, isStartupCommand, maskPaths, reportedTokens, statusPresentation,
} from "./eventPresentation.js";
import { CommandTimeline } from "./timeline.js";

export class TrajectoryProjection {
  constructor() {
    this.records = [];
    this.delivered = new Set();
    this.resetRows();
  }

  resetRows() {
    this.entries = new Map();
    this.commands = new CommandTimeline();
    this.workspace = null;
    this.tokens = [];
    this.reply = null;
    this.issue = null;
    this.firstOffset = null;
    this.lastOffset = null;
    this.terminal = false;
  }

  accept(record, eventId, summaries) {
    const id = eventId == null ? null : String(eventId);
    if (id !== null && this.delivered.has(id)) return null;
    if (id !== null) this.delivered.add(id);
    const duration = summaries?.[record.event.item?.id]?.observed_duration_ns;
    return { record, key: id === null ? `record:${this.records.length}` : `delivery:${id}`, duration };
  }

  append(record, eventId) {
    const accepted = this.accept(record, eventId);
    if (!accepted) return false;
    this.records.push(accepted);
    this.project(accepted);
    return true;
  }

  prepend(events, summaries) {
    const earlier = events.map(({ record, event_id: id }) => this.accept(record, id, summaries)).filter(Boolean);
    if (!earlier.length) return;
    const terminal = this.terminal;
    this.records = [...earlier, ...this.records];
    this.resetRows();
    for (const record of this.records) this.project(record);
    if (terminal) this.finish();
  }

  project({ record, key, duration }) {
    const { event, elapsed_ns: offset } = record;
    if (Number.isFinite(offset) && offset >= 0) {
      this.firstOffset = Math.min(this.firstOffset ?? offset, offset);
      this.lastOffset = Math.max(this.lastOffset ?? offset, offset);
    }
    this.observeTurn(event);
    const command = this.commands.observe(record, duration);
    if (command) {
      this.renderCommand(command);
      return;
    }
    this.renderEvent(record, key);
  }

  observeTurn(event) {
    switch (event.type) {
      case "turn.started":
        if (typeof event.cwd === "string" && event.cwd.startsWith("/")) this.workspace = event.cwd;
        this.tokens = [];
        this.reply = null;
        this.issue = null;
        break;
      case "turn.completed":
        this.tokens = reportedTokens(event);
        break;
      case "turn.failed":
        this.issue = errorMessage(event) ?? this.issue;
        break;
      case "error":
        this.issue = errorMessage(event) ?? this.issue;
        break;
    }
  }

  renderCommand(command) {
    const key = `command:${command.id}`;
    const status = statusPresentation(command.status);
    const commandText = maskPaths(displayCommand(command.command), this.workspace);
    this.entries.set(key, {
      key, command, commandText, title: commandTitle(commandText), ...status,
      startup: isStartupCommand(displayCommand(command.command), this.workspace),
      preview: status.failed ? commandErrorPreview(command, this.workspace) : null,
      duration: formatDuration(command.observedDurationNs),
      offset: formatOffset(command.records[0].elapsed_ns),
      startedOffset: formatOffset(command.startedOffsetNs),
      completedOffset: formatOffset(command.completedOffsetNs),
    });
  }

  renderEvent(record, key) {
    const { event } = record;
    const item = event.item;
    const toolKey = typeof item?.id === "string" ? JSON.stringify([item.type, item.id]) : key;
    const previous = this.entries.get(toolKey);
    const tool = describeTool(record, previous?.tool);
    if (tool) {
      const records = [...(previous?.records ?? []), record];
      this.entries.set(toolKey, {
        key: toolKey, tool: { ...tool, name: maskPaths(tool.name, this.workspace) },
        records, event, ...statusPresentation(tool.status),
        toolPreview: maskPaths(tool.preview, this.workspace),
        toolError: maskPaths(tool.error, this.workspace),
        offset: formatOffset(records[0].elapsed_ns),
      });
      return;
    }
    const entry = { key, event, ...describeEvent(event, this.workspace), offset: formatOffset(record.elapsed_ns) };
    this.entries.set(key, entry);
    if (event.type === "item.completed" && (entry.markdown?.trim() || entry.omitted)) this.reply = entry;
  }

  finish() {
    this.terminal = true;
    for (const command of this.commands.finish()) this.renderCommand(command);
    for (const [key, entry] of this.entries) {
      if (entry.tool && !entry.tool.terminal) {
        const status = "result_unconfirmed";
        this.entries.set(key, { ...entry, tool: { ...entry.tool, status }, ...statusPresentation(status) });
      }
    }
  }

  snapshot() {
    const rows = [...this.entries.values()];
    return {
      rows, workspace: this.workspace, tokens: this.tokens, reply: this.reply, terminal: this.terminal,
      firstOffset: this.firstOffset, lastOffset: this.lastOffset, issue: maskPaths(this.issue, this.workspace),
      actions: rows.filter((entry) => entry.command || entry.tool),
      failures: rows.filter((entry) => (entry.command || entry.tool) && entry.failed),
    };
  }
}

export function actionCallId(entry) {
  return entry.command?.id ?? (entry.tool ? entry.event?.item?.id : null) ?? entry.key;
}

export function observedFileChanges(actions, workspace) {
  const files = new Map();
  for (const entry of actions) {
    if (entry.event?.item?.type !== "file_change" || entry.tool.status !== "completed"
        || entry.tool.conflictingResults) continue;
    if (!Array.isArray(entry.tool.input)) continue;
    for (const change of entry.tool.input) {
      const path = change?.path;
      if (typeof path !== "string" || !path.trim()) continue;
      files.set(path, { path: maskPaths(path, workspace), entry });
    }
  }
  return [...files.values()];
}

export function runOutcome({ exit_code: exitCode, error }, turn) {
  if (error === "capture_unavailable") return { status: "unconfirmed", detail: "A captura ficou indisponível.", terminal: false };
  if (error === "event_too_large") return { status: "failed", detail: "Captura incompleta. Um evento excedeu o limite de 8 MiB.", terminal: true };
  if (error === "execution_failed") return { status: "failed", detail: "O executor encerrou sem informar um código de saída.", terminal: true };
  const failed = turn === "turn.failed" || (Number.isInteger(exitCode) && exitCode !== 0);
  if (failed) {
    const detail = Number.isInteger(exitCode) ? `Código de saída ${exitCode}.` : null;
    return { status: "failed", detail, terminal: true };
  }
  const status = exitCode === 0 && turn === "turn.completed" ? "completed" : "unconfirmed";
  return { status, detail: null, terminal: true };
}

export function savedOutcome(page) {
  if (page.status === "active" || page.continuation?.state === "active") return { status: "active", detail: null, terminal: false };
  if (page.status === "failed" && page.exit_code == null && page.last_turn !== "turn.failed") {
    return { status: "failed", detail: "Falhou durante a captura.", terminal: true };
  }
  return runOutcome(page, page.last_turn);
}
