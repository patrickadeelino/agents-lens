import { onUnmounted, ref, shallowRef } from "vue";
import { loadRunHistory } from "./runs.js";
import { savedOutcome, TrajectoryProjection } from "./trajectoryProjection.js";

export function useRunTrajectory() {
  let projection = new TrajectoryProjection();
  let earlierRequest;
  const data = shallowRef(projection.snapshot());
  const nextBefore = ref(null);
  const loadingEarlier = ref(false);
  const historyError = ref("");

  function cancelEarlier() {
    earlierRequest?.abort();
    earlierRequest = undefined;
    loadingEarlier.value = false;
  }

  function reset() {
    cancelEarlier();
    projection = new TrajectoryProjection();
    data.value = projection.snapshot();
    nextBefore.value = null;
    historyError.value = "";
  }

  function initialize(page) {
    projection.prepend(page.events, page.commands);
    if (savedOutcome(page).terminal) projection.finish();
    data.value = projection.snapshot();
    nextBefore.value = page.next_before;
  }

  function append(record, id) {
    if (!projection.append(record, id)) return false;
    data.value = projection.snapshot();
    return true;
  }

  function finish() {
    projection.finish();
    data.value = projection.snapshot();
  }

  async function loadEarlier(runId) {
    if (loadingEarlier.value || nextBefore.value == null || !runId) return;
    const request = new AbortController();
    earlierRequest = request;
    loadingEarlier.value = true;
    historyError.value = "";
    try {
      const page = await loadRunHistory(runId, request.signal, nextBefore.value);
      request.signal.throwIfAborted();
      projection.prepend(page.events, page.commands);
      data.value = projection.snapshot();
      nextBefore.value = page.next_before;
    } catch {
      if (!request.signal.aborted) historyError.value = "Falha ao carregar eventos anteriores. Tente novamente.";
    } finally {
      if (earlierRequest === request) {
        earlierRequest = undefined;
        loadingEarlier.value = false;
      }
    }
  }

  onUnmounted(cancelEarlier);
  return { data, nextBefore, loadingEarlier, historyError, cancelEarlier, reset, initialize, append, finish, loadEarlier };
}
