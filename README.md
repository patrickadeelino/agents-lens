# Agent Lens

Agent Lens is a local web interface for Codex App Server. Select a scratch directory, send a prompt, and follow the agent's messages, tool activity and subagents live. The browser can reconnect to a running turn, and saved conversations can continue in the same Codex thread.

The app reports elapsed time measured by the backend and token counts reported by Codex.

## Requirements

- A Linux graphical session with Zenity installed at `/usr/bin/zenity`.
- Docker with the Compose plugin, Python 3.13 and `uv`.
- A native Codex executable and a valid OpenAI API key. The live flow was validated with Codex **0.171.0** and `gpt-6.2-sol`. Use the native executable path for `--codex-bin`.

## Start locally

Open a Bash terminal at the repository root and start the host executor. Keep it running.

```bash
cd apps/api-lens
uv sync --locked
CODEX_BIN=/absolute/path/to/native/codex
"$CODEX_BIN" --version
read -rsp 'OpenAI API key: ' CODEX_API_KEY
printf '\n'
printf '%s\n' "$CODEX_API_KEY" | uv run --no-sync python -m executor \
  --codex-bin "$CODEX_BIN" --model gpt-6.2-sol
```

In a second terminal at the repository root, start the application. The executor creates the IPC directory before Compose mounts it.

```bash
AGENT_LENS_HOST_SOCKET_DIR="$PWD/.local/codex-executor/catalog/ipc" \
  docker compose up -d --build --wait backend frontend
curl --fail --silent --show-error http://127.0.0.1:8080/ready
```

The readiness endpoint responds with HTTP 204 when the API can reach the host executor. If the interface reports **Executor unavailable**, check that the executor is running and that Compose mounts its IPC directory. Then use **Check connection**.

Open [Agent Lens](http://127.0.0.1:8080/). Select a disposable folder outside the Agent Lens repository, send a prompt, and open **View trajectory** on the turn. The executor can modify the selected files. The service binds to loopback. Keep prompts, command output, local captures and Codex state private when sharing screenshots or recordings.

To stop the local application, run `docker compose down` at the repository root and stop the executor with Ctrl+C. Compose keeps the capture volume. The [optional observability setup](observability/README.md) describes logs and traces.

## Verify without model calls

Run the backend checks from `apps/api-lens`.

```sh
uv sync --locked
uv run --no-sync ruff check .
uv run --no-sync mypy .
uv run --no-sync pytest -q
```

Run the frontend checks from `apps/web` using Node.js 24.

```sh
npm ci
node --test
npm run build
```
