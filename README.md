# Agent Lens

Agent Lens is a local web interface for Codex App Server. Select a scratch folder, send a prompt, and follow the agent's messages, tool activity and subagents live. The browser can reconnect to a running turn, and saved conversations can continue in the same Codex thread.

The app reports elapsed time measured by the backend and token counts reported by Codex.

## Requirements

- Docker with the Compose plugin.
- An OpenAI API key with access to the model. The live flow was validated with Codex **0.171.0** and `gpt-6.2-sol`.
- Tested on Linux x86_64 with kernel 5.15. Other systems have not been tested.

## Quick start

```sh
cp .env.example .env
```

Set `CODEX_API_KEY` in `.env`, then start everything:

```sh
docker compose up --build --wait
```

Open [Agent Lens](http://localhost:8080/), use **Select folder**, send a prompt and open **View trajectory** on the turn. The first build downloads the official Codex release, about 200 MB, and checks its SHA-256.

Stop with `docker compose down`. Conversations are kept in Docker volumes.

## Choose what the agent can reach

Only one folder of your machine is mounted: [`workspace/`](workspace/) by default. To work on your own projects, set their parent folder in `.env`:

```sh
AGENT_LENS_WORKSPACE=/absolute/path/to/your/projects
```

**Select folder** then lists its subfolders, and each conversation keeps the one you chose. Run `docker compose up --wait` again after changing the value. **The agent can modify the files in the chosen folder.** Do not choose a folder that holds your `.env` or other secrets.

On Linux, if `id -u` or `id -g` is not `1000`, set `AGENT_LENS_UID` and `AGENT_LENS_GID` in `.env` before the first build, so the agent can edit your files.

The agent's commands have no network access and can use `git`, `rg` and Python 3. To give it other tools, add them to [`Dockerfile.executor`](apps/api-lens/Dockerfile.executor).

## Security

- The interface listens on loopback, accepts only the `localhost` and `127.0.0.1` host names and **has no authentication**.
- Codex runs each command in its own sandbox: no network, and access limited to the chosen folder and basic system paths.
- That sandbox needs namespaces that Docker blocks by default, so the **executor container runs with AppArmor and seccomp disabled**. It has no Linux capabilities, cannot gain privileges and runs as a regular user, but it is less isolated from your machine than a default container.
- The API key is read from `.env` or your shell and passed to the executor as a Compose secret. A `.env` file keeps it in plain text on disk.
- Prompts, command output and captures are stored unencrypted. Review them before sharing screenshots or recordings.

If turns fail to start commands on Ubuntu 23.10 or later, the host may be restricting unprivileged user namespaces. This setup has not been tested there.

The [optional observability setup](observability/README.md) describes logs and traces.

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
