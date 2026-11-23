function observedDurationNs(start, completion) {
  const startNs = start?.elapsed_ns;
  const completionNs = completion?.elapsed_ns;
  if (!Number.isFinite(startNs) || !Number.isFinite(completionNs) || completionNs < startNs) {
    return null;
  }
  return completionNs - startNs;
}

function resultsConflict(previous, current) {
  return ["status", "exit_code", "aggregated_output"].some((key) => (
    previous[key] != null && current[key] != null && previous[key] !== current[key]
  ));
}

function commandRow(id, { start, completion, historyDurationNs, records, conflictingResults }) {
  const startedItem = start?.event.item;
  const completedItem = completion?.event.item;
  return {
    id,
    command: completedItem?.command ?? startedItem?.command ?? null,
    status: completedItem
      ? completedItem.status ?? "result_unconfirmed"
      : startedItem.status ?? "in_progress",
    output: completedItem?.aggregated_output ?? startedItem?.aggregated_output ?? null,
    exitCode: completedItem?.exit_code ?? null,
    startedOffsetNs: start?.elapsed_ns ?? null,
    completedOffsetNs: completion?.elapsed_ns ?? null,
    observedDurationNs: observedDurationNs(start, completion) ?? historyDurationNs ?? null,
    startMissing: Boolean(completion && !start),
    conflictingResults,
    records,
  };
}

// Each instance observes the events of one run.
export class CommandTimeline {
  #commands = new Map();

  observe(record, durationFromHistory) {
    const { type, item } = record.event;
    if (type !== "item.started" && type !== "item.completed") return;
    if (item?.type !== "command_execution" || typeof item.id !== "string") return;

    const previous = this.#commands.get(item.id);
    const start = previous?.start ?? (type === "item.started" ? record : null);
    const isCompletion = type === "item.completed";
    const completion = isCompletion ? record : previous?.completion;
    const historyDurationNs = isCompletion ? durationFromHistory : previous?.historyDurationNs;
    const records = [...(previous?.row.records ?? []), record];
    const conflictingResults = Boolean(previous?.row.conflictingResults
      || (isCompletion && previous?.row.records.some(({ event }) => (
        event.type === "item.completed" && resultsConflict(event.item, item)
      ))));
    const row = commandRow(item.id, { start, completion, historyDurationNs, records, conflictingResults });

    this.#commands.set(item.id, { start, completion, historyDurationNs, row });
    return row;
  }

  finish() {
    const changedRows = [];
    for (const command of this.#commands.values()) {
      if (command.completion || command.row.status === "result_unconfirmed") continue;
      command.row = { ...command.row, status: "result_unconfirmed" };
      changedRows.push(command.row);
    }
    return changedRows;
  }

  get rows() {
    return Array.from(this.#commands.values(), ({ row }) => row);
  }
}
