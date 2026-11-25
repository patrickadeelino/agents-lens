import {
  commandErrorPreview, commandTitle, describeEvent, describeTool, displayCommand, errorMessage,
  formatDuration, formatOffset, isStartupCommand, maskPaths, reportedTokens, statusPresentation,
} from "./eventPresentation.js";
import { CommandTimeline } from "./timeline.js";
import { t } from "./messages.js";

export class TrajectoryProjection {
  constructor() {
    this.records = [];
    this.delivered = new Set();
    this.resetRows();
  }

  resetRows() {
    this.entries = new Map();
    this.delegates = new Map();
    this.delegationCount = 0;
    this.hasRootStart = false;
    this.waitTargets = new Map();
    this.rootThread = this.rootThreadHint ?? null;
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

  prepend(events, summaries, rootThread) {
    this.rootThreadHint = rootThread ?? this.rootThreadHint;
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
    if (event.type === "thread.started") {
      this.rootThread = event.thread_id;
      this.hasRootStart = true;
    }
    if (event.type.startsWith("delegation.")) {
      this.projectDelegation(record, key);
      return;
    }
    this.observeTurn(event);
    const command = this.commands.observe(record, duration);
    if (command) {
      this.renderCommand(command);
      return;
    }
    this.renderEvent(record, key);
  }

  delegate(thread) {
    if (!this.delegates.has(thread)) {
      this.delegates.set(thread, { thread, turns: new Map(), state: "running", terminal: false });
    }
    return this.delegates.get(thread);
  }

  delegateTurn(thread, turn) {
    const branch = this.delegate(thread);
    if (!branch.turns.has(turn)) {
      const projection = new TrajectoryProjection();
      projection.workspace = this.workspace;
      branch.turns.set(turn, projection);
    }
    return branch.turns.get(turn);
  }

  projectDelegation(record, key) {
    const { event } = record;
    if (event.type === "delegation.edge") {
      const child = this.delegate(event.child_thread_id);
      if (child.edge) return;
      child.edge = event;
      this.delegationCount += 1;
      child.index = event.child_index ?? (this.hasRootStart ? this.delegationCount : null);
      const owner = event.parent_thread_id === this.rootThread
        ? this : this.delegateTurn(event.parent_thread_id, event.parent_turn_id);
      owner.entries.set(key, { key, branchId: child.thread, event, observedAt: record.elapsed_ns, offset: formatOffset(record.elapsed_ns) });
      return;
    }
    const child = this.delegate(event.thread_id);
    const observation = event.observation;
    if (event.type === "delegation.incomplete") {
      child.state = "incomplete";
      child.terminal = true;
    } else if (observation?.type === "subagent.wait") {
      const wait = JSON.stringify([event.source_thread_id, event.turn_id, event.item_id]);
      const targets = this.waitTargets.get(wait) ?? [];
      targets.push(child.thread);
      this.waitTargets.set(wait, targets);
      child.summary = observation.state?.message ?? child.summary;
      if (["completed", "failed", "errored", "shutdown", "interrupted"].includes(observation.state?.status)) {
        child.terminal = true;
        child.state = observation.state.status === "completed" ? "completed" : "failed";
      }
    } else if (observation?.type === "subagent.completed") {
      child.name = observation.item?.agentPath?.split("/").filter(Boolean).at(-1);
      if (!child.terminal) child.state = "completed";
      child.terminal = true;
    } else if (observation) {
      this.projectChild(child, event.turn_id, { ...record, event: observation }, key);
    }
  }

  projectChild(child, turn, record, key) {
    const projection = this.delegateTurn(child.thread, turn);
    const { event } = record;
    if (event.type === "turn.started") {
      child.state = "running";
      child.terminal = false;
    }
    if (event.type === "turn.completed") {
      child.terminal = true;
      child.state = event.status === "completed" ? "completed" : event.status === "failed" ? "failed" : "interrupted";
    }
    projection.project({ record, key });
    if (event.type === "turn.completed") projection.finish();
  }

  treeRows(projection = this, ancestors = [], scope = null) {
    return [...projection.entries.values()].map((entry) => {
      if (entry.branchId && !ancestors.includes(entry.branchId)) {
        const branch = this.delegates.get(entry.branchId);
        const path = [...ancestors, branch.thread];
        const rows = [...branch.turns].flatMap(([turn, child]) => this.treeRows(child, path, [branch.thread, turn]));
        const turns = [...branch.turns.values()].filter((turn) => turn.firstOffset != null);
        const duration = turns.length ? formatDuration(
          Math.max(...turns.map((turn) => turn.lastOffset)) - Math.min(...turns.map((turn) => turn.firstOffset)),
        ) : null;
        return {
          ...entry, key: branch.thread, branch: true, rows, ancestors, duration, terminal: branch.terminal || this.terminal,
          actionCount: flattenTrajectory(rows).filter((row) => row.command || row.tool).length,
          state: !branch.terminal && this.terminal ? "incomplete" : branch.state,
          label: [t.delegation.label(branch.index), branch.name].filter(Boolean).join(" · "),
          summary: maskPaths(branch.summary, this.workspace),
          failed: branch.state === "failed" || rows.some((row) => row.failed),
        };
      }
      entry = this.labelWait(entry, scope);
      if (!scope) return entry;
      return {
        ...entry, key: JSON.stringify([...scope, entry.key]), ancestors,
        callId: `child:${JSON.stringify([...scope, actionCallId(entry)])}`,
        terminal: projection.terminal || this.terminal,
      };
    });
  }

  labelWait(entry, scope) {
    const item = entry.event?.item;
    if (item?.type !== "collab_tool_call" || item.tool !== "wait" || !entry.tool) return entry;
    const owner = scope?.[0] ?? this.rootThread;
    const targets = new Set();
    for (const { event } of entry.records ?? []) {
      const call = event.item;
      for (const thread of call.receiver_thread_ids ?? []) targets.add(thread);
      for (const thread of Object.keys(call.agents_states ?? {})) targets.add(thread);
    }
    for (const [identity, threads] of this.waitTargets) {
      const [source, turn, id] = JSON.parse(identity);
      if (source === owner && id === item.id && (!scope || turn === scope[1])) {
        for (const thread of threads) targets.add(thread);
      }
    }
    const branches = [...targets].map((thread) => this.delegates.get(thread));
    if (!branches.length || branches.some((branch) => !branch?.index || branch.edge?.parent_thread_id !== owner)) {
      return entry;
    }
    const labels = branches.sort((left, right) => left.index - right.index)
      .map((branch) => branch.name ? `${branch.index} · ${branch.name}` : String(branch.index));
    const list = new Intl.ListFormat("en-US").format(labels);
    const name = t.delegation.wait(labels.length, list);
    return { ...entry, tool: { ...entry.tool, name } };
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
    for (const branch of this.delegates.values()) {
      for (const turn of branch.turns.values()) turn.finish();
    }
    for (const command of this.commands.finish()) this.renderCommand(command);
    for (const [key, entry] of this.entries) {
      if (entry.tool && !entry.tool.terminal) {
        const status = "result_unconfirmed";
        this.entries.set(key, { ...entry, tool: { ...entry.tool, status }, ...statusPresentation(status) });
      }
    }
  }

  snapshot() {
    const rows = this.treeRows();
    return {
      rows, workspace: this.workspace, tokens: this.tokens, reply: this.reply, terminal: this.terminal,
      firstOffset: this.firstOffset, lastOffset: this.lastOffset, issue: maskPaths(this.issue, this.workspace),
      actions: rows.filter((entry) => entry.command || entry.tool),
      failures: rows.filter((entry) => (entry.command || entry.tool) && entry.failed),
    };
  }
}

export function actionCallId(entry) {
  return entry.callId ?? entry.command?.id ?? (entry.tool ? entry.event?.item?.id : null) ?? entry.key;
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
  if (error === "capture_unavailable") return { status: "unconfirmed", detail: t.outcome.captureUnavailable, terminal: false };
  if (error === "event_too_large") return { status: "failed", detail: t.outcome.eventTooLarge, terminal: true };
  if (error === "execution_failed") return { status: "failed", detail: t.outcome.executionFailed, terminal: true };
  const failed = turn === "turn.failed" || (Number.isInteger(exitCode) && exitCode !== 0);
  if (failed) {
    const detail = Number.isInteger(exitCode) ? t.outcome.exitCode(exitCode) : null;
    return { status: "failed", detail, terminal: true };
  }
  const status = exitCode === 0 && turn === "turn.completed" ? "completed" : "unconfirmed";
  return { status, detail: null, terminal: true };
}

export function savedOutcome(page) {
  if (page.status === "active" || page.continuation?.state === "active") return { status: "active", detail: null, terminal: false };
  if (page.status === "failed" && page.exit_code == null && page.last_turn !== "turn.failed") {
    return { status: "failed", detail: t.outcome.failedDuringCapture, terminal: true };
  }
  return runOutcome(page, page.last_turn);
}

export function flattenTrajectory(rows) {
  return rows.flatMap((entry) => entry.branch ? [entry, ...flattenTrajectory(entry.rows)] : [entry]);
}
