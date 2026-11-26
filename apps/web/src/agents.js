import { computed, inject } from "vue";
import { t } from "./messages.js";

/** The agent of every conversation saved before there was a choice. */
export const DEFAULT_AGENT = "codex";

export function agentName(agent) {
  return t.agents.names[agent] ?? t.agents.unknown;
}

/** Keeps the current choice while it can run; otherwise takes the first one that can. */
export function chooseAgent(selected, available) {
  return !available.length || available.includes(selected) ? selected : available[0];
}

/** Returns no agents when the list cannot be read, which leaves the current choice as it is. */
export async function loadAgents(request = fetch) {
  try {
    const response = await request("/agents", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!response.ok) return [];
    const { agents } = await response.json();
    return Array.isArray(agents) ? agents.filter((agent) => Object.hasOwn(t.agents.names, agent)) : [];
  } catch {
    return [];
  }
}

/** The name of the agent of the conversation on screen. */
export function useAgentName() {
  const agent = inject("agent", null);
  return computed(() => agentName(agent?.value ?? DEFAULT_AGENT));
}
