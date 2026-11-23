const key = "agent-lens:recent-runs";

export function recentRuns() {
  try {
    const stored = JSON.parse(sessionStorage.getItem(key) ?? "[]");
    return Array.isArray(stored) ? stored.filter((id) => typeof id === "string").slice(0, 5) : [];
  } catch {
    return [];
  }
}

export function rememberRun(id, current) {
  const runs = [id, ...current.filter((run) => run !== id)].slice(0, 5);
  try {
    sessionStorage.setItem(key, JSON.stringify(runs));
  } catch {
    // The in-memory list still works for the current visit.
  }
  return runs;
}
