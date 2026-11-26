export class RunHttpError extends Error {
  constructor(status, detail) {
    super(`HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function* readFrames(body) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += value;

      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer)) !== null) {
        const lines = buffer.slice(0, boundary.index).split(/\r?\n/);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const name = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
        const data = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");

        if (name && data) yield { name, data: JSON.parse(data) };
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The reader may already have failed or been aborted.
    }
    reader.releaseLock();
  }
}

export function streamPrompt(prompt, callbacks, workspace, agent) {
  return streamNewRun("/runs", prompt, callbacks, { workspace_id: workspace?.id, workspace_run_id: workspace?.runId, agent });
}

export function streamContinuation(runId, prompt, callbacks) {
  return streamNewRun(`/runs/${encodeURIComponent(runId)}/continue`, prompt, callbacks);
}

async function streamNewRun(url, prompt, { signal, onAccepted, onRecord }, fields = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ prompt, ...fields }),
    signal,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new RunHttpError(response.status, typeof payload?.detail === "string" ? payload.detail : null);
  }
  const runId = response.headers.get("X-Run-ID");
  if (!response.body || !runId) throw new RunHttpError(response.status);

  onAccepted(runId);
  for await (const frame of readFrames(response.body)) {
    if (frame.name === "record") onRecord(frame.data);
    if (frame.name === "end") return frame.data;
  }
  return null;
}

export async function loadRunHistory(runId, signal, before = null) {
  const path = `/runs/${encodeURIComponent(runId)}/events`;
  const url = before === null ? path : `${path}?${new URLSearchParams({ before })}`;
  while (true) {
    const response = await fetch(url, { signal });
    if (response.status === 409) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      continue;
    }
    if (!response.ok) throw new RunHttpError(response.status);
    return response.json();
  }
}

function readEventSource(url, { signal, onOpen, onRecord, onReconnect }) {
  signal.throwIfAborted();

  return new Promise((resolve, reject) => {
    const source = new EventSource(url);

    function settle(callback, value) {
      source.close();
      signal.removeEventListener("abort", onAbort);
      callback(value);
    }

    function onAbort() {
      settle(reject, signal.reason);
    }

    signal.addEventListener("abort", onAbort, { once: true });
    source.addEventListener("open", onOpen);
    source.addEventListener("record", (message) => {
      try {
        onRecord(JSON.parse(message.data), message.lastEventId || undefined);
      } catch (error) {
        settle(reject, error);
      }
    });
    source.addEventListener("end", (message) => {
      try {
        settle(resolve, JSON.parse(message.data));
      } catch (error) {
        settle(reject, error);
      }
    });
    source.addEventListener("error", () => {
      if (onReconnect && source.readyState === EventSource.CONNECTING) {
        onReconnect();
        return;
      }
      settle(reject, new Error("Event stream interrupted"));
    });
  });
}

export function streamSavedRun(runId, after, callbacks) {
  return readEventSource(`/runs/${encodeURIComponent(runId)}/stream?after=${after}`, callbacks);
}
