import { t } from "./messages.js";

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

export function workspaceName(path) {
  return path.split("/").filter(Boolean).at(-1) || path;
}

export function groupConversations(threads) {
  const groups = new Map();
  for (const thread of threads) {
    const path = thread.workspace || null;
    if (!groups.has(path)) {
      groups.set(path, {
        path,
        name: path ? workspaceName(path) : t.sidebar.earlier,
        runId: thread.root_run_id,
        threads: [],
      });
    }
    groups.get(path).threads.push(thread);
  }
  return [...groups.values()];
}
