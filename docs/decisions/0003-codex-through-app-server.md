# 0003 — Drive Codex through App Server

**Status:** Accepted

## Context

The first integration used `codex exec --json`. It was enough for a single agent, but it did not give the child agents' activity attributed to each of them. An isolated experiment with delegation found attributed child command output only through App Server over stdio.

## Decision

**The executor starts one `codex app-server` process per turn** and talks to it over standard input and output. It starts or resumes a thread, sends one turn and stops the process when the turn ends or the reader goes away.

**Nothing is assumed about the session.** Before sending the prompt, the executor checks that the approval policy, the permission profile and the working folder are the ones it asked for. It refuses every request that the server makes back to it. The API key is held in memory by Codex and not written to its credential store.

**Notifications are translated** into the capture's own event format (see [0001](0001-observed-events-and-projections.md)). Token usage of a resumed turn excludes the earlier turns.

**Subagents are captured by attribution.** A child thread is linked to its parent by the `subAgentActivity` notification that reports it started. The child's events are saved inside delegation records, so that its messages, commands and outcome never change the parent's turn.

**Events that arrive before their link are held briefly**, for at most 1 second, 128 events or 8 MiB. The parent turn does not wait for its children. A child with no final event when the capture ends is marked as incomplete.

## Alternatives

- **Keep `codex exec --json`.** No protocol adapter would be needed, but the child activity could not be shown.
- **Keep one App Server for many turns.** It would save process startup, and add routing between requests and a lifecycle to manage.
- **Read Codex's private session files.** It would add a second reader of an undocumented format, with its own timing.
- **Pass child events through as ordinary events.** A child message, command ID or final event could change what the parent turn shows.

## Consequences

- Each turn is isolated in its own process, and resuming relies on the thread that Codex saved.
- Every turn pays the cost of starting a process.
- The adapter depends on a protocol that is still changing, and enables its experimental API. The Codex version is pinned to 0.171.0, and other versions are not tested.
- An event that arrives after the holding limits without a known parent is dropped.
- A single event larger than 8 MiB ends the capture, keeping what was already saved.

## Related code

- [Protocol and translation](../../apps/api-lens/src/executor/codex/app_server.py)
- [Delegation](../../apps/api-lens/src/executor/codex/app_server_delegation.py)
- [Command line and permissions](../../apps/api-lens/src/executor/codex/catalog_run.py)
- [Process lifecycle](../../apps/api-lens/src/executor/process/codex_process.py)
- [Pinned version](../../apps/api-lens/Dockerfile.executor)
