# Local observability

Agent Lens writes structured operational logs to stderr for live runs and replay. The optional `observability` profile in `compose.yaml` sends logs and spans via OTLP/HTTP to the OpenTelemetry Collector, which batches and forwards them to OpenObserve OSS.

## Start the stack

Use the repository's ignored `.env` for local settings. On a fresh checkout, copy `.env.example` to `.env` and fill in the local OpenObserve password. For an existing `.env`, add the observability settings from the template and keep the existing Codex key assignment. OpenObserve requires at least eight characters including upper and lower case, a number, and a special character.

```sh
docker compose up -d --build
```

Compose loads `.env` automatically. With `COMPOSE_PROFILES=observability` and `AGENT_LENS_OTEL_ENABLED=1`, the application starts with the Collector and OpenObserve. A checkout with the default Compose settings starts with local JSON logs. For live runs, start the existing executor on the host and add its socket directory to `.env` or export the following settings. Then use the same `docker compose up` command.

```sh
AGENT_LENS_HOST_SOCKET_DIR=/absolute/path/to/the/socket-directory
```

Use the directory containing `executor.sock`. The backend mounts it read-only and saves live captures in the `captures` volume.

Set these variables in the shell used to start the existing host executor.

```sh
export AGENT_LENS_OTEL_ENABLED=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:14318
```

The backend sends OTLP to `http://otel-collector:4318` over the Compose network. Both published ports bind to loopback. Set `AGENT_LENS_OTLP_PORT` or `AGENT_LENS_OPENOBSERVE_PORT` to override the default host ports `14318` and `15080`. Use the configured host OTLP port in the executor's endpoint.

## Inspect a run

Open [OpenObserve](http://127.0.0.1:15080/) and sign in with the local credentials. Select the `agent_lens_logs` stream for operational events and find the associated trace in `agent_lens_traces` using `trace_id`. A live `run_id` also identifies its capture directory. On disconnect, the SSE response and the capture worker report their outcomes separately.

The live waterfall starts at `POST /runs`. The `run.capture` and `run.sse` spans are siblings, so a client disconnect can end delivery while the capture worker keeps saving records. `run.sse` marks the first record and the terminal frame handed to ASGI, with the count and maximum flush-to-send delay on the span. Inspect `codex.subprocess` for the first stdout read and the first envelope handed to ASGI on the executor. Inspect `executor.request` for the first envelope and terminal message received by the backend. These timestamps mark observations by each service. `capture.write_result` reports the filesystem write outcome independently of the Codex exit code.

Each `codex.command` span is a child of `executor.request`. Its duration is the elapsed time between receipt of a command's `item.started` and `item.completed` in the backend. Use the validated item ID to match it with the captured trajectory. A short span can reflect start and completion events arriving together. The request span reports `agent_lens.command.missing_start_count` and `agent_lens.command.missing_completion_count` when an event has no match.

Filter the log stream by `run_id` and sort by `timestamp` to follow the first record. `executor.request.started` marks the backend's call. The subsequent milestones are `executor.subprocess.first_event_read`, `executor.request.first_event_received`, `run.capture.first_record_saved`, and `run.sse.first_record_sent`. The SSE milestone means the ASGI send returned. Compare timestamps across stages. Each `duration_ms` measures time from the start of that log's own stage. The subprocess and capture summaries report `event_count`, with the capture count taken after flushing records.

Live SSE logs `run.sse.record_sent` after each record and `run.sse.end_sent` after the terminal frame. The `event_count` field is the number of records sent so far, starting at 1 on the first record. The terminal frame reports the total records sent and the process exit code when available. These logs mark sends accepted by ASGI. Each record log includes `flush_to_sse_us`, measured on the backend's monotonic clock from the trace file flush through queueing, SSE serialization and the ASGI send. It also includes the allowlisted `event_type` and, for item events, `item_type`. Unknown types are `other`. For command items, `command_status` and `command_exit_code` report the observed values when available. The command status is independent of SSE delivery and the final run result.

Logs include UTC time, service, component, event, and outcome. Error codes and duration help explain the result. The original Codex events stay in the private JSONL capture. Prompts, subprocess output, local paths, and credentials are excluded from telemetry.

The `observability-data` volume stores the local logs and traces. A Collector outage can make telemetry delivery incomplete while the API, SSE, and private capture continue to operate.
