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

const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const datedYear = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });

export function activityLabel(value, now = new Date()) {
  const date = new Date(value ?? "");
  if (!Number.isFinite(date.getTime())) return null;
  if (date.toDateString() === now.toDateString()) return t.sidebar.today;
  return (date.getFullYear() === now.getFullYear() ? shortDate : datedYear).format(date);
}

export function threadLink(thread) {
  const params = thread.thread_id ? { thread_id: thread.thread_id } : { run_id: thread.latest_run_id };
  return `?${new URLSearchParams(params)}`;
}

export function groupConversations(threads) {
  const groups = new Map();
  for (const thread of threads) {
    const path = thread.workspace;
    if (!groups.has(path)) {
      groups.set(path, {
        path,
        name: workspaceName(path),
        runId: thread.root_run_id,
        threads: [],
      });
    }
    groups.get(path).threads.push(thread);
  }
  return [...groups.values()];
}
