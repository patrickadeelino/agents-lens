// Follow the one active run, independently of the conversation on screen.
export function createRunTracker({ watch, isActive, onChange, wait }) {
  let current = null;
  let request;

  function set(runId) {
    current = runId;
    onChange(runId);
  }

  async function follow(runId, signal) {
    while (!signal.aborted) {
      try {
        await watch(runId, signal);
        break;
      } catch {
        if (signal.aborted) return;
        // A lost connection does not mean the run finished.
        if (!await isActive(runId, signal).catch(() => true)) break;
        await wait();
      }
    }
    if (!signal.aborted) set(null);
  }

  return {
    track(runId) {
      if (runId === current) return;
      request?.abort();
      request = new AbortController();
      set(runId);
      follow(runId, request.signal);
    },
    stop() {
      request?.abort();
      current = null;
    },
  };
}
