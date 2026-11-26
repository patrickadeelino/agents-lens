# 0004 — Run the executor as an isolated service

**Status:** Accepted

## Context

Running the agent needs three things that the rest of the application does not: the API key, write access to a folder of the person's machine and the ability to run commands. The project should also start with `docker compose up`, without installing Codex or other tools on the host.

Codex isolates each command with Bubblewrap, which Docker's default AppArmor and seccomp profiles block.

## Decision

**The executor is a separate service and a separate part of the code.** Its image ships only the executor and the shared package, and a test keeps the executor and the backend from importing each other.

**Only the executor holds the sensitive access.** It receives the API key as a Compose secret, on standard input, and mounts the one folder set in `AGENT_LENS_WORKSPACE`. The backend has neither. The two talk over a Unix socket in a shared volume, and the executor publishes no port.

**The interface chooses folders by reference.** It lists subfolders of the mounted one and receives a selection ID. Hidden folders, links that leave the root and paths outside it are refused.

**The container is relaxed only where the sandbox requires it.** The executor sets `seccomp=unconfined` and `apparmor=unconfined`. It also drops every capability, sets `no-new-privileges` and runs as the person's user ID.

**Commands keep the Codex sandbox.** They have no network access and reach only the chosen folder and basic system paths.

**The Codex release is pinned** to 0.171.0 and checked against its SHA-256 when the image is built.

## Alternatives

| Alternative | Why not |
|---|---|
| Run the executor on the host | Needs Codex and `uv` installed, and a second terminal |
| Disable the Codex sandbox and rely on the container | Agent commands would get network access and could reach the key |
| Relax only seccomp, or only AppArmor | Both failed in the probe below |
| Keep the executor and the backend in one package | The code would not show the boundary, and each image would carry the other service |

## Evidence

Disposable containers without network or key, Codex 0.171.0, all capabilities dropped, `no-new-privileges`, user ID 1000, on Linux x86_64 with kernel 5.15:

| Docker profiles | Sandboxed command |
|---|---|
| Defaults | Bubblewrap could not create a namespace |
| seccomp unconfined | `Failed to make / slave: Permission denied` |
| AppArmor unconfined | Could not create a namespace |
| Both unconfined | Reading and writing the allowed folder worked; a sibling folder, the network and other processes' environment were blocked |

In the built image, sandboxed commands could not read the key, the executor history or the socket.

## Consequences

- The executor is **less isolated from the host than a default container**. Without those two profiles, more of the host kernel is reachable if the sandbox is escaped.
- The agent can modify the chosen folder. Choosing one that contains a `.env` file or other secrets exposes them to it.
- The key is kept in plain text when it is stored in `.env`.
- Commands can only use the tools built into the image, such as `git`, `rg` and Python 3.
- There is no authentication. The interface listens on loopback and refuses other host names.
- Ubuntu 23.10 or later, macOS, Windows and ARM have not been tested.

## Related code

- [Services](../../compose.yaml)
- [Executor image](../../apps/api-lens/Dockerfile.executor)
- [Folder rules](../../apps/api-lens/src/executor/workspace/workspace_folders.py)
- [Sandbox permissions](../../apps/api-lens/src/executor/agents/codex/catalog_run.py)
- [Host names](../../apps/web/nginx.conf)
- [Service boundaries](../../apps/api-lens/tests/architecture/test_layers.py)
