# Writable Catalog API lab

The host executor seeds `.local/codex-executor/catalog/workspace` once from this directory and keeps later edits. Its private Codex state and socket are siblings at `.local/codex-executor/catalog/state` and `.local/codex-executor/catalog/ipc/executor.sock`. Use a dedicated Compose project so its capture volume remains separate from other Agent Lens instances. The web server is bound to loopback.

From the repository root, start the executor in a terminal. Set `CATALOG_CODEX_BIN` to a native Codex binary with App Server support (verified on 0.171.0) and `CATALOG_MODEL` to the desired model. The executor starts a stdio App Server for each submitted turn and reuses the private Codex state when resuming a thread. The credential is entered silently and sent on standard input; do not put it in a command argument or commit it.

```zsh
read -rs 'CATALOG_KEY?Codex API key: '
print -rn -- "$CATALOG_KEY" | (cd apps/api-lens && uv run python -m executor --codex-bin "$CATALOG_CODEX_BIN" --model "$CATALOG_MODEL")
unset CATALOG_KEY
```

In another terminal, from the repository root:

```zsh
AGENT_LENS_PORT=8080 AGENT_LENS_HOST_SOCKET_DIR="$PWD/.local/codex-executor/catalog/ipc" docker compose -p agent-lens-catalog up -d --build backend frontend
```

Open `http://127.0.0.1:8080/`. Stop Compose with `docker compose -p agent-lens-catalog down` and stop the host executor with Ctrl-C. Do not use `down -v`: the named capture volume stores the conversation history. Starting both again reuses the catalog workspace and private Codex state. The browser offers an offline replay separately.

Inside the seeded workspace, `python -m unittest` runs the synthetic Catalog API tests; `python catalog.py` starts the service on loopback port 8091.
