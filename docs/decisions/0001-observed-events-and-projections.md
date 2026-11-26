# 0001 — Save observed events and derive the state from them

**Status:** Accepted

## Context

A Codex turn arrives as a stream of events about items such as commands, messages and file changes. Items can complete out of order. Some report only their completion. A process that dies can leave a command that never reports an end. The browser can reconnect and receive an event again.

The interface has to show what happened, including what is not known.

## Decision

Keep two things apart.

**The capture is the record of what was observed.** The API appends one event per line to a JSONL file and flushes it before sending it to the browser. Each line carries the time elapsed since the capture started reading, measured when the API receives the event.

The events are not the raw App Server notifications. The executor translates them into the capture's own format, leaves out the ones the trajectory does not use, such as the echo of the prompt, and masks the API key.

**The state is derived from the capture.** Nothing stores the status of a command as the source of truth. It is computed by replaying the events, in the browser for the trajectory and in the API for history pages and turn summaries.

The projection follows these rules:

- An item keeps the position where it was first observed, even when its result arrives later.
- A completion without an observed start is kept and marked as such. Its duration stays unknown unless history reports one.
- A command with no completion when the capture ends is **result unconfirmed**. It is not counted as a success or a failure.
- Different results for the same command are flagged as conflicting.
- An event delivered twice with the same ID is applied once.
- A turn succeeds only if it reported completion **and** the process exited with code 0.
- A subagent's activity is nested under its parent only when the events attribute it to that thread.

## Alternatives

- **Keep only the current state of each item.** It is simpler, but loses the order of events, the evidence behind each status and the ability to replay.
- **Save the notifications as App Server sends them.** Nothing would be left out, but the saved history and its readers would depend on a protocol that this project does not control.
- **Use a database or an event bus.** A local application for one person does not need either yet.

## Consequences

- A run can be replayed, and a bug in a projection can be fixed without running the model again.
- The same capture serves the live view, the reconnection and the history.
- What the translation leaves out is lost. A projection cannot recover it later.
- The rules exist twice, in Python and in JavaScript, and must be kept consistent.
- A line is flushed to the operating system but not forced to disk, so a power failure can lose the latest events.
- Captures are plain files with no encryption. Prompts and command output are readable by anyone with access to them.

## Related code

- [Capture and flush](../../apps/api-lens/src/backend/application/capture/capture.py)
- [Event translation](../../apps/api-lens/src/executor/codex/app_server.py)
- [Command state in the API](../../apps/api-lens/src/backend/domain/turn/command_execution.py)
- [Command timeline in the browser](../../apps/web/src/timeline.js)
- [Turn outcome](../../apps/api-lens/src/backend/domain/turn/turn_outcome.py)
