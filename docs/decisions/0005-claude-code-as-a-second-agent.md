# 0005 — Add Claude Code behind the same capture format

**Status:** Accepted

## Context

Agent Lens was built around Codex. A second agent makes it possible to watch both with the same interface. Claude Code differs in ways that matter here:

- Run headless, it writes a stream of JSON messages and takes no requests back.
- Its stream has no exit code field. A failed command states it in the first line of its output.
- A turn with a subagent in the background ended with two final results.
- A failed login was reported with the subtype `success` and an error flag.
- Its sandbox covers shell commands only, and by default they can read the whole filesystem.

## Decision

**The executor drives each agent through an adapter.** An adapter builds the command line and turns one process into events. The process lifecycle, the size limit, secret masking and telemetry are shared.

**Claude Code is translated into the existing capture format** (see [0001](0001-observed-events-and-projections.md)). The backend and the interface keep one projection. Tools without a type of their own, such as reading a file, became a generic `tool_call` item.

**Nothing is filled in that was not observed.** A turn is completed only when its latest result is clean and the process exits with zero. Without a result it stays unconfirmed. A failed command gets the exit code written in its output, and a successful one gets none. A subagent counts as completed only when that is reported.

**A conversation belongs to one agent.** The agent is saved with it and used by every follow-up, because a session can only be resumed by the agent that created it. Earlier conversations belong to Codex. An agent that is not available is refused before anything is saved.

**Both agents run in the same executor service** (see [0004](0004-isolate-the-executor.md)), one turn at a time. Because that puts both keys in one container, the Claude Code settings:

- require the sandbox and allow no command outside it;
- deny reading the workspace root, the executor's data and the secrets, and open only the chosen folder again;
- remove the API key from the environment of every command;
- use the nested mode, which an unprivileged container needs.

**Claude Code is optional.** Its key is declared in a second Compose file, because Compose requires the variable of every secret it declares. The binary is pinned to 2.1.327 and checked against its SHA-256.

## Alternatives

| Alternative | Why not |
|---|---|
| Define a new neutral event format | Would change the backend, the interface and how saved captures are read, for the same screen |
| Report exit code `0` for successful commands | The agent does not report it |
| Show other tools as MCP calls | The interface would state something false about them |
| Run one executor service per agent | Stronger separation between the keys, with a second image, socket and routing to maintain. It remains the option if one container cannot keep them apart |
| One Compose file with a config that defaults to empty | `docker compose config` printed the key, and with empty content Compose mounted the project folder, including `.env`, into the container |

## Evidence

Claude Code 2.1.327 in the executor image, driven by this repository's adapter against a local stand-in for the Messages API, without network, with fake secrets, on Linux x86_64 with kernel 5.15. The stand-in chooses the tool calls, so this shows what the command line allows, not how a model behaves.

| Shell command | Default reads | With these settings |
|---|---|---|
| Read the other agent's key | Allowed | Blocked |
| Read a sibling folder, directly or through a link | Allowed | Blocked |
| Find the API key in its environment | Found | Absent |
| Write outside the folder, reach the network | Blocked | Blocked |

"Default reads" had only the executor's own data denied. The file tools were refused outside the folder in both cases. A subagent's commands had the same restrictions.

Translation is tested with three recorded runs of the real model.

## Consequences

- The shared format keeps names that come from Codex, and every difference has to be translated into it.
- The exit code of a failed command depends on the wording of its output. A successful test shows no result in the test summary.
- Token counts cover the main agent, not its subagents. Input includes the cached part, as in the Codex captures.
- The keys are separated by each agent's sandbox rather than by containers. The nested mode is weaker than the default: commands see the executor's process list and can read the container's system files, including the application's code.
- The executor image includes Claude Code, about 370 MB, even when it is off. Turning it on takes two lines in `.env`.
- Claude Code creates a `.claude` folder in the chosen folder.
- **Not tested yet:** the model through this app, background shell commands, parallel subagents, the sandbox in a resumed turn, the Codex sandbox against the second key, and ARM.

## Related code

- [Adapter interface](../../apps/api-lens/src/executor/agents/base.py)
- [Command line and sandbox settings](../../apps/api-lens/src/executor/agents/claude/run.py)
- [Translation](../../apps/api-lens/src/executor/agents/claude/translation.py)
- [Subagents](../../apps/api-lens/src/executor/agents/claude/subagents.py)
- [Agents at startup](../../apps/api-lens/src/executor/startup.py)
- [Recorded runs](../../apps/api-lens/tests/unit/executor/agents/claude/recordings)
- [Optional Compose file](../../compose.claude.yaml)
- [Executor image](../../apps/api-lens/Dockerfile.executor)
