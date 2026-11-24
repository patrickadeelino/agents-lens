export function resolveInspectedRun(thread, requestedRunId) {
  if (!requestedRunId) return null;
  if (!thread.turns.some((turn) => turn.run_id === requestedRunId)) {
    throw new Error("Unknown conversation turn");
  }
  return requestedRunId;
}

export function inspectionUrl(currentUrl, selectedRunId, provisionalRunId = null) {
  const url = new URL(currentUrl);
  const runId = selectedRunId ?? (url.searchParams.has("thread_id") ? null : provisionalRunId);
  if (runId) url.searchParams.set("run_id", runId);
  else url.searchParams.delete("run_id");
  url.hash = "";
  return url;
}
