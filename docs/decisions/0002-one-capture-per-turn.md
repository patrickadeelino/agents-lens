# 0002 — Keep one capture per turn

**Status:** Accepted

## Context

A conversation is a series of prompts sent to the same Codex thread. Each turn has its own process, outcome and timing, and any of them can fail. The executor runs one turn at a time, and either service can stop in the middle of one.

The history has to survive a restart, never fork a conversation and not leave it stuck after a failure.

## Decision

**Each accepted prompt gets its own capture**, a directory with the session (prompt, folder, submission time and previous run), the events and, at the end, the result. A conversation is the chain formed by these captures. Codex keeps the thread; Agent Lens keeps the links and what it observed.

**A turn has at most one successor.** The new turn is linked to the previous one with a file that can only be created once. Of two concurrent follow-ups, one gets the link and the other is refused. Saved files are never rewritten. Only the latest turn can be continued.

**A thread ID is saved only when the events confirm it.** If a turn reports a different thread from the one it should resume, the conversation is not continued from it.

**An attempt that failed without confirming a session can still be continued.** The follow-up resumes the thread of the nearest earlier turn that confirmed one. If there is none, the conversation cannot be resumed.

**Prompts are refused while a turn is running**, before anything is saved. This check is not atomic: two prompts can pass at the same instant, and the executor rejects the second, which becomes a failed attempt that can be retried.

**At startup, the API closes the turns that have no result.** They are recorded as unconfirmed, not as failed, because nobody observed how they ended. This is only safe with a single API process.

**A damaged conversation is isolated.** It is left out of the list and counted, and the others keep working. Conversations that share a thread ID are left out too.

## Alternatives

- **Append later turns to the first capture.** It would mix the outcomes of different processes and need a different history format.
- **Remove the link when a follow-up is rejected.** It would break the rule that saved files do not change, and would not cover a failure that happens after the turn starts, such as a timeout.
- **Only check whether the executor is free before linking.** The check races with other prompts and does not cover a timeout either.
- **Queue prompts or run turns in parallel.** A queue adds cancellation and restart states, and parallel turns affect isolation. Neither is justified for one person on one machine.
- **Show damaged conversations, or recover their readable turns.** A damaged one may have no reliable title or identity, and a partial chain could be continued from the wrong turn.

## Consequences

- Replay and live streaming work the same way for every turn.
- A transient failure no longer blocks a conversation.
- Which turn is running is kept in memory, and recovery assumes one API process. Running several would need shared coordination.
- There is no index: listing reads every saved conversation, and opening one still reads the others to detect a repeated thread ID.
- The follow-up of a failed attempt resumes the Codex thread, which may or may not have kept that attempt.
- An interrupted turn keeps its events, but its card shows no partial reply.
- There is no compatibility with earlier capture formats.

## Related code

- [Continuation rules](../../apps/api-lens/src/backend/application/conversation/run_continuation.py)
- [Continuing a conversation](../../apps/api-lens/src/backend/application/conversation/continue_conversation.py)
- [Chain of turns](../../apps/api-lens/src/backend/domain/conversation/lineage.py)
- [Saved files and the single link](../../apps/api-lens/src/backend/infrastructure/storage/capture_files.py)
- [Recovery at startup](../../apps/api-lens/src/backend/application/capture/interrupted_runs.py)
- [Conversation list](../../apps/api-lens/src/backend/application/conversation/conversations.py)
