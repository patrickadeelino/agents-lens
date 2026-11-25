<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, shallowRef, watch } from "vue";
import ConversationSidebar from "./ConversationSidebar.vue";
import ConversationTurn from "./ConversationTurn.vue";
import HistoricalTrajectory from "./HistoricalTrajectory.vue";
import TrajectoryPane from "./TrajectoryPane.vue";
import { inspectionUrl, resolveInspectedRun, workspaceName } from "./conversationNavigation.js";
import { CONNECTION } from "./connectionState.js";
import { maskPaths } from "./eventPresentation.js";
import { loadRunHistory, RunHttpError, streamContinuation, streamPrompt, streamSavedRun } from "./runs.js";
import { runOutcome, savedOutcome } from "./trajectoryProjection.js";
import { useRunFeedback } from "./runFeedback.js";
import { useRunTrajectory } from "./useRunTrajectory.js";

const view = ref("home");
const navigationError = ref("");
const threads = ref([]);
const threadsLoading = ref(false);
const threadsError = ref(false);
const conversation = shallowRef(null);
const activeRunId = ref(null);
const inspectedRunId = ref(null);
const focusInspection = ref(false);
const linkedCall = ref(readCall());
const prompt = ref("");
const promptInput = ref(null);
const turnScroller = ref(null);
const conversationHeading = ref(null);
const compactHeading = ref(null);
const historyButton = ref(null);
const drawer = ref(false);
const sidebarQuery = window.matchMedia("(min-width: 1201px)");
const wideSidebar = ref(sidebarQuery.matches);
const busy = ref(false);
const composerError = ref("");
const selectedWorkspace = ref(null);
const pickingWorkspace = ref(false);
const workspaceError = ref("");
const executorReady = ref(null);
const checkingExecutor = ref(false);
const continuationError = ref("");
const continuationDrafts = reactive(new Map());
const observed = reactive(new Map());
const paneScroll = new Map();
const model = useRunTrajectory();
const connection = ref(CONNECTION.idle);
const outcome = shallowRef(null);
const liveLoading = ref(false);
const feedback = useRunFeedback(connection);
const { announcement, lastUpdateSeconds, recovered, announce } = feedback;

let operation;
let locationRequest;
let listRequest;

const turns = computed(() => conversation.value?.turns ?? []);
const selectedTurn = computed(() => turns.value.find((turn) => turn.run_id === inspectedRunId.value));
const selectedNumber = computed(() => turns.value.findIndex((turn) => turn.run_id === inspectedRunId.value) + 1 || null);
const liveTurn = computed(() => turns.value.find((turn) => turn.run_id === activeRunId.value));
const latestTurn = computed(() => turns.value.at(-1));
const activeTurn = computed(() => latestTurn.value?.status === "active" ? latestTurn.value : null);
const activeNumber = computed(() => activeTurn.value ? turns.value.length : null);
const hasPane = computed(() => selectedTurn.value != null);
const canContinue = computed(() => executorReady.value && !busy.value && !activeTurn.value && conversation.value?.continuation?.state === "available");
const continuationDraft = computed({
  get: () => continuationDrafts.get(conversation.value?.root_run_id) ?? "",
  set: (value) => continuationDrafts.set(conversation.value?.root_run_id, value),
});
const continuationNote = computed(() => {
  if (activeTurn.value) return `Disponível quando o turno ${activeNumber.value} terminar.`;
  if (busy.value) return "Enviando o pedido…";
  if (executorReady.value !== true && conversation.value?.continuation?.state === "available") {
    return executorReady.value === false ? "A retomada exige o executor local." : "Verificando o executor local…";
  }
  return ({
    ephemeral: "Esta execução não salvou uma sessão do Codex.",
    active: "Aguarde a execução terminar para continuar.",
    superseded: "Esta conversa tem uma execução mais recente.",
  })[conversation.value?.continuation?.state] ?? "O Codex não confirmou uma sessão disponível para esta execução.";
});
const sidebarThreads = computed(() => {
  const current = conversation.value;
  if (!current || current.legacy) return threads.value;
  return [current, ...threads.value.filter((thread) => thread.root_run_id !== current.root_run_id)]
    .sort((a, b) => String(b.last_activity_at ?? "").localeCompare(String(a.last_activity_at ?? "")));
});
const liveAction = computed(() => model.data.value.actions.findLast((entry) =>
  (entry.command ?? entry.tool).status === "in_progress"));
const isCurrentPane = computed(() => inspectedRunId.value === activeRunId.value);
const paneProps = computed(() => ({
  key: inspectedRunId.value,
  runId: selectedTurn.value?.run_id,
  threadId: conversation.value?.thread_id,
  summary: selectedTurn.value?.summary,
  turnNumber: selectedNumber.value,
  prompt: selectedTurn.value?.prompt,
  status: turnLabel(selectedTurn.value?.status),
  active: !!activeTurn.value && activeTurn.value.run_id === inspectedRunId.value,
  activeTurnNumber: activeNumber.value,
  activeConnection: connection.value,
  linkedCall: linkedCall.value,
  initialScroll: paneScroll.get(inspectedRunId.value) ?? 0,
  focusOnOpen: focusInspection.value,
}));

watch([model.data, model.nextBefore], ([data, before]) => {
  if (activeRunId.value) rememberEvidence(activeRunId.value, data, before);
});

function turnEvidence(turn) {
  return turn.summary?.actions != null ? turn.summary : observed.get(turn.run_id);
}

function rememberEvidence(runId, data, before) {
  const partial = before != null || !data.terminal;
  if (!data.actions.length && (partial || !data.rows.length)) return;
  const current = observed.get(runId);
  if (current && !current.partial && partial) return;
  if (current && partial && current.actions > data.actions.length) return;
  observed.set(runId, { actions: data.actions.length, failures: data.failures.length, partial, workspace: data.workspace });
}

function turnLabel(status) {
  return ({ active: "Em andamento", completed: "Concluído", failed: "Falhou", unconfirmed: "Resultado não confirmado" })[status];
}

function presentedTurn(turn) {
  if (turn.run_id !== activeRunId.value || turn.assistant_markdown) return turn;
  return { ...turn, assistant_markdown: model.data.value.reply?.markdown ?? null };
}

function readCall() {
  return new URLSearchParams(window.location.hash.slice(1)).get("call");
}

function writeLocation(url, state, replace = false) {
  if (replace) window.history.replaceState(state, "", url);
  else window.history.pushState(state, "", url);
  linkedCall.value = readCall();
}

function canonicalize() {
  const thread = conversation.value;
  if (!thread) return;
  const url = new URL(window.location.href);
  if (thread.thread_id) {
    url.searchParams.set("thread_id", thread.thread_id);
    if (inspectedRunId.value) url.searchParams.set("run_id", inspectedRunId.value);
    else url.searchParams.delete("run_id");
  } else {
    url.searchParams.delete("thread_id");
    url.searchParams.set("run_id", inspectedRunId.value ?? thread.latest_run_id);
  }
  writeLocation(url, { ...window.history.state, closed: !inspectedRunId.value }, true);
}

function inspectTurn(runId) {
  if (inspectedRunId.value === runId) return closeInspection();
  focusInspection.value = true;
  const openedFromConversation = !inspectedRunId.value;
  inspectedRunId.value = runId;
  const url = inspectionUrl(window.location.href, runId);
  writeLocation(url, { inspection: openedFromConversation });
}

function inspectActive() {
  const runId = activeTurn.value?.run_id;
  if (!runId || inspectedRunId.value === runId) return;
  paneScroll.delete(runId);
  inspectTurn(runId);
}

async function restoreTurnFocus(runId) {
  await nextTick();
  const card = [...(turnScroller.value?.children ?? [])].find((node) => node.dataset.turnRun === runId);
  (card?.querySelector(".inspect-turn") ?? conversationHeading.value)?.focus({ preventScroll: true });
}

function closeInspection() {
  const previous = inspectedRunId.value;
  if (!previous) return;
  if (window.history.state?.inspection) {
    window.history.back();
    return;
  }
  inspectedRunId.value = null;
  const url = inspectionUrl(window.location.href, null, conversation.value?.latest_run_id);
  writeLocation(url, { closed: true }, true);
  restoreTurnFocus(previous);
}

function changeActionCall(call) {
  const url = new URL(window.location.href);
  url.hash = call ? new URLSearchParams({ call }).toString() : "";
  writeLocation(url, window.history.state, true);
}

function rememberScroll(top) {
  if (inspectedRunId.value) paneScroll.set(inspectedRunId.value, top);
}

function closeDrawer(restoreFocus = true) {
  drawer.value = false;
  if (restoreFocus) nextTick(() => historyButton.value?.focus({ preventScroll: true }));
}

function onSidebarChange(event) {
  wideSidebar.value = event.matches;
  if (event.matches) closeDrawer(false);
}

function onEscape(event) {
  if (event.key !== "Escape") return;
  if (drawer.value) closeDrawer();
  else if (inspectedRunId.value && !wideSidebar.value) closeInspection();
}

async function loadThreads() {
  listRequest?.abort();
  const request = new AbortController();
  listRequest = request;
  threadsLoading.value = true;
  threadsError.value = false;
  try {
    const response = await fetch("/threads", { signal: request.signal, cache: "no-store" });
    if (!response.ok) throw new RunHttpError(response.status);
    const payload = await response.json();
    request.signal.throwIfAborted();
    threads.value = payload.threads;
  } catch {
    if (!request.signal.aborted) threadsError.value = true;
  } finally {
    if (listRequest === request) threadsLoading.value = false;
  }
}

async function fetchConversation(path, signal) {
  const response = await fetch(path, { signal, cache: "no-store" });
  if (!response.ok) throw new RunHttpError(response.status);
  const detail = await response.json();
  signal.throwIfAborted();
  return detail;
}

function byRun(runId, signal) {
  return fetchConversation(`/threads/by-run/${encodeURIComponent(runId)}`, signal);
}

function cancelOperation() {
  operation?.abort();
  operation = undefined;
  busy.value = false;
}

function resetLive(runId = null) {
  model.reset();
  feedback.reset();
  activeRunId.value = runId;
  outcome.value = null;
  connection.value = CONNECTION.idle;
  liveLoading.value = false;
}

function resetView() {
  locationRequest?.abort();
  cancelOperation();
  resetLive();
  conversation.value = null;
  inspectedRunId.value = null;
  focusInspection.value = false;
  continuationError.value = "";
  navigationError.value = "";
}

function beginOperation() {
  cancelOperation();
  const request = new AbortController();
  operation = request;
  busy.value = true;
  return request;
}

function finishOperation(request) {
  if (operation !== request) return;
  operation = undefined;
  busy.value = false;
  liveLoading.value = false;
}

async function focusPrompt() {
  await nextTick();
  if (view.value === "home" && window.matchMedia("(min-width: 901px) and (pointer: fine)").matches) {
    promptInput.value?.focus({ preventScroll: true });
  }
}

function newConversation(group = null) {
  selectedWorkspace.value = group ? { path: group.path, runId: group.runId } : null;
  workspaceError.value = "";
  if (view.value !== "home" || window.location.search || window.location.hash) writeLocation("/", null);
  closeDrawer(false);
  resetView();
  view.value = "home";
  composerError.value = "";
  loadThreads();
  focusPrompt();
}

function onThreadClick(event) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  closeDrawer(false);
  const url = new URL(event.currentTarget.href);
  if (url.search === window.location.search && !window.location.hash && view.value === "thread") return;
  writeLocation(url, null);
  restoreLocation({ focus: true });
}

async function scrollConversationToEnd(focus = false) {
  await nextTick();
  if (turnScroller.value) turnScroller.value.scrollTop = turnScroller.value.scrollHeight;
  if (focus) focusConversationHeading();
}

function focusConversationHeading() {
  const heading = window.matchMedia("(max-width: 900px)").matches ? compactHeading.value : conversationHeading.value;
  heading?.focus({ preventScroll: true });
}

function provisionalTurn(runId, text) {
  return {
    run_id: runId, prompt: text, assistant_markdown: null, submitted_at: new Date().toISOString(), status: "active",
  };
}

function provisionalConversation(runId, text, legacy = false) {
  const turn = provisionalTurn(runId, text);
  return {
    thread_id: null, root_run_id: runId, latest_run_id: runId, legacy,
    workspace: legacy ? null : selectedWorkspace.value?.path,
    title: legacy ? "Execução salva" : (text.trim().replace(/\s+/g, " ").slice(0, 80) || "Conversa"),
    last_activity_at: turn.submitted_at, status: "active", turns: [turn], continuation: { state: "active" },
  };
}

function acceptedRun(runId, text, origin) {
  const current = conversation.value;
  resetLive(runId);
  const turn = provisionalTurn(runId, text);
  if (origin && current) {
    conversation.value = {
      ...current, latest_run_id: runId, status: "active", last_activity_at: turn.submitted_at,
      turns: [...current.turns, turn], continuation: { state: "active" },
    };
    continuationDrafts.delete(current.root_run_id);
  } else {
    conversation.value = provisionalConversation(runId, text);
    prompt.value = "";
    view.value = "thread";
  }
  if (!origin) {
    const base = new URL(window.location.href);
    base.searchParams.set("run_id", runId);
    writeLocation(base, { closed: true });
    writeLocation(base, { inspection: true });
  }
  if (!inspectedRunId.value || inspectedRunId.value === origin) {
    inspectedRunId.value = runId;
    focusInspection.value = false;
    linkedCall.value = null;
    const url = new URL(window.location.href);
    url.hash = "";
    writeLocation(url, window.history.state, true);
  }
  canonicalize();
  connection.value = CONNECTION.receiving;
  scrollConversationToEnd(!origin && !hasPane.value);
  if (!origin && hasPane.value) nextTick(() => {
    const destination = window.matchMedia("(max-width: 1200px)").matches
      ? document.getElementById("trajectory-title") : conversationHeading.value;
    destination?.focus({ preventScroll: true });
  });
  loadThreads();
}

function appendRecord(record, id) {
  if (model.append(record, id)) feedback.observe(record, model.data.value.workspace);
}

function applyOutcome(result) {
  if (result.terminal) model.finish();
  outcome.value = result;
  const current = conversation.value;
  if (current && current.latest_run_id === activeRunId.value) {
    conversation.value = {
      ...current, status: result.status,
      turns: current.turns.map((turn) => turn.run_id !== activeRunId.value ? turn : {
        ...turn, status: result.status, assistant_markdown: model.data.value.reply?.markdown ?? turn.assistant_markdown,
      }),
    };
  }
}

function observedTurn(previous, { event }) {
  return ["turn.started", "turn.completed", "turn.failed"].includes(event.type) ? event.type : previous;
}

function finishCapture(result, turn) {
  const final = runOutcome(result, turn);
  applyOutcome(final);
  connection.value = final.terminal ? CONNECTION.ended : CONNECTION.interrupted;
  if (final.status !== "completed") announce(turnLabel(final.status));
}

async function refreshConversation(runId, signal, continuationFallback = null) {
  try {
    const detail = await byRun(runId, signal);
    signal.throwIfAborted();
    conversation.value = detail;
    canonicalize();
    loadThreads();
    return conversation.value;
  } catch {
    signal.throwIfAborted();
    if (continuationFallback && conversation.value?.latest_run_id === runId) {
      conversation.value = { ...conversation.value, continuation: continuationFallback };
    }
    return null;
  }
}

async function readAcceptedRun(runId, signal) {
  liveLoading.value = true;
  const page = await loadRunHistory(runId, signal);
  signal.throwIfAborted();
  model.reset();
  model.initialize(page, conversation.value?.thread_id);
  liveLoading.value = false;
  const saved = savedOutcome(page);
  if (saved.terminal && conversation.value?.status !== "active") {
    applyOutcome(saved);
    connection.value = CONNECTION.ended;
    await refreshConversation(runId, signal, page.continuation);
    return;
  }
  let turn = page.last_turn;
  const result = await streamSavedRun(runId, page.latest_event_id ?? 0, {
    signal,
    onOpen: () => { connection.value = CONNECTION.receiving; },
    onRecord(record, id) {
      appendRecord(record, id);
      turn = observedTurn(turn, record);
    },
    onReconnect: () => { connection.value = CONNECTION.reconnecting; },
  });
  signal.throwIfAborted();
  finishCapture(result, turn);
  await refreshConversation(runId, signal);
}

function resumeObservation(runId) {
  const request = beginOperation();
  if (activeRunId.value !== runId) resetLive(runId);
  connection.value = CONNECTION.loading;
  return readAcceptedRun(runId, request.signal).catch(() => {
    if (!request.signal.aborted) connection.value = CONNECTION.interrupted;
  }).finally(() => finishOperation(request));
}

function submitError(error) {
  if (error instanceof RunHttpError) {
    return maskPaths(error.detail) || `Falha ao enviar o pedido (HTTP ${error.status}). Seu rascunho foi preservado.`;
  }
  return "Envio não confirmado. Confira a conversa mais recente antes de reenviar.";
}

async function recoverSubmission(runId, signal) {
  connection.value = CONNECTION.reconnecting;
  try {
    await readAcceptedRun(runId, signal);
  } catch {
    if (!signal.aborted) connection.value = CONNECTION.interrupted;
  }
}

async function checkExecutor() {
  if (checkingExecutor.value) return;
  checkingExecutor.value = true;
  try {
    const response = await fetch("/ready", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    executorReady.value = response.status === 204;
  } catch {
    executorReady.value = false;
  } finally {
    checkingExecutor.value = false;
  }
}

async function chooseWorkspace() {
  if (!executorReady.value || pickingWorkspace.value || busy.value) return;
  pickingWorkspace.value = true;
  workspaceError.value = "";
  try {
    const response = await fetch("/workspace-picker", {
      method: "POST", headers: { "X-Agent-Lens": "workspace-picker" },
    });
    if (response.status === 204) return;
    const payload = await response.json();
    if (!response.ok) {
      if (response.status === 503) checkExecutor();
      throw new Error(payload.detail);
    }
    selectedWorkspace.value = payload;
  } catch (error) {
    workspaceError.value = error.message;
  } finally {
    pickingWorkspace.value = false;
  }
}

async function sendPrompt(text, origin = null) {
  if (!executorReady.value || busy.value || !text.trim() || (origin ? !canContinue.value : (!selectedWorkspace.value || pickingWorkspace.value))) return;
  composerError.value = "";
  continuationError.value = "";
  const request = beginOperation();
  const { signal } = request;
  let accepted;
  let turn;
  try {
    const callbacks = {
      signal,
      onAccepted(runId) {
        signal.throwIfAborted();
        accepted = runId;
        acceptedRun(runId, text, origin);
      },
      onRecord(record) {
        signal.throwIfAborted();
        appendRecord(record);
        turn = observedTurn(turn, record);
      },
    };
    const result = origin ? await streamContinuation(origin, text, callbacks) : await streamPrompt(text, callbacks, selectedWorkspace.value);
    signal.throwIfAborted();
    if (result) {
      finishCapture(result, turn);
      await refreshConversation(accepted, signal);
    } else if (accepted) await recoverSubmission(accepted, signal);
  } catch (error) {
    if (signal.aborted) return;
    if (error instanceof RunHttpError && error.status === 503) checkExecutor();
    if (accepted) await recoverSubmission(accepted, signal);
    else if (!origin) composerError.value = submitError(error);
    else if (error instanceof RunHttpError && error.status === 409) {
      continuationError.value = "A execução de origem já foi continuada. Seu rascunho foi preservado.";
      const refreshed = await refreshConversation(origin, signal).catch(() => null);
      if (refreshed?.status === "active") resumeObservation(refreshed.latest_run_id);
    } else continuationError.value = submitError(error);
  } finally {
    finishOperation(request);
  }
}

async function resolveLocation(params, signal) {
  const threadId = params.get("thread_id");
  const requested = params.get("run_id");
  if (threadId) return fetchConversation(`/threads/${encodeURIComponent(threadId)}`, signal);
  try {
    return await byRun(requested, signal);
  } catch (error) {
    signal.throwIfAborted();
    if (!(error instanceof RunHttpError) || ![404, 503].includes(error.status)) throw error;
    return provisionalConversation(requested, null, true);
  }
}

async function restoreLocation({ focus = false } = {}) {
  resetView();
  const params = new URLSearchParams(window.location.search);
  const requested = params.get("run_id");
  loadThreads();
  if (!params.has("thread_id") && !requested) {
    view.value = "home";
    focusPrompt();
    return;
  }
  view.value = "loading";
  const request = new AbortController();
  locationRequest = request;
  try {
    const detail = await resolveLocation(params, request.signal);
    request.signal.throwIfAborted();
    conversation.value = detail;
    inspectedRunId.value = window.history.state?.closed ? null : resolveInspectedRun(detail, requested);
    focusInspection.value = !!inspectedRunId.value;
    view.value = "thread";
    canonicalize();
    scrollConversationToEnd(focus && !inspectedRunId.value);
    if (detail.status === "active") resumeObservation(detail.latest_run_id);
  } catch {
    if (request.signal.aborted) return;
    conversation.value = null;
    view.value = "home";
    navigationError.value = "Não foi possível abrir esta conversa.";
  }
}

function navigateHistory() {
  const params = new URLSearchParams(window.location.search);
  const current = conversation.value;
  const requested = params.get("run_id");
  const locationThread = params.get("thread_id");
  const same = current && (locationThread
    ? current.thread_id === locationThread
    : requested != null && current.turns.some((turn) => turn.run_id === requested));
  linkedCall.value = readCall();
  if (same) {
    const previous = inspectedRunId.value;
    const selected = window.history.state?.closed ? null : requested;
    if (selected && !current.turns.some((turn) => turn.run_id === selected)) return restoreLocation();
    if (previous === selected) return;
    focusInspection.value = !!selected;
    inspectedRunId.value = selected;
    if (current.thread_id && !locationThread) canonicalize();
    if (previous && !selected) restoreTurnFocus(previous);
    return;
  }
  restoreLocation();
}

function synchronizeHash() {
  linkedCall.value = readCall();
}

onMounted(() => {
  sidebarQuery.addEventListener("change", onSidebarChange);
  window.addEventListener("popstate", navigateHistory);
  window.addEventListener("hashchange", synchronizeHash);
  window.addEventListener("keydown", onEscape);
  window.addEventListener("focus", checkExecutor);
  checkExecutor();
  restoreLocation();
});

onUnmounted(() => {
  sidebarQuery.removeEventListener("change", onSidebarChange);
  window.removeEventListener("popstate", navigateHistory);
  window.removeEventListener("hashchange", synchronizeHash);
  window.removeEventListener("keydown", onEscape);
  window.removeEventListener("focus", checkExecutor);
  locationRequest?.abort();
  listRequest?.abort();
  cancelOperation();
});
</script>

<template>
  <div class="app-shell" :class="{ 'has-trajectory': hasPane, 'drawer-visible': drawer }">
    <p class="visually-hidden" role="status">{{ announcement }}</p>
    <ConversationSidebar :threads="sidebarThreads" :current-root="conversation?.root_run_id" :loading="threadsLoading"
      :error="threadsError" :drawer="drawer"
      @new="newConversation" @open="onThreadClick" @retry="loadThreads" @close="closeDrawer()" />

    <header class="compact-navigation" :inert="drawer">
      <button ref="historyButton" type="button" class="icon-button" aria-label="Abrir histórico" aria-controls="conversation-sidebar"
        :aria-expanded="drawer" @click="drawer = true">
        <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
      </button>
      <strong ref="compactHeading" tabindex="-1">{{ conversation?.title || "Agent Lens" }}</strong>
      <button type="button" class="icon-button compact-new" aria-label="Nova conversa" @click="newConversation()">
        <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
      </button>
    </header>

    <main class="conversation-main" :class="{ 'home-main': view !== 'thread' }" :inert="drawer || (hasPane && !wideSidebar)">
      <div v-if="executorReady === false" class="connection-notice executor-notice">
        <p role="status">Executor indisponível. Inicie o executor no host e confira o socket.</p>
        <button type="button" class="secondary" :disabled="checkingExecutor" @click="checkExecutor">Verificar conexão</button>
      </div>
      <section v-if="view === 'home'" class="home-composer" aria-labelledby="composer-title">
        <p v-if="navigationError" class="composer-status" role="alert">
          {{ navigationError }} <button type="button" class="link-button" @click="restoreLocation()">Tentar novamente</button>
        </p>
        <form id="run-form" class="run-form" @submit.prevent="sendPrompt(prompt)">
          <p class="view-caption">Nova conversa</p>
          <h1 id="composer-title"><label for="prompt">O que o Codex deve fazer?</label></h1>
          <textarea id="prompt" ref="promptInput" v-model="prompt" name="prompt" rows="5" required :readonly="busy"
            placeholder="Leia o README e identifique como iniciar o serviço."
            @keydown.ctrl.enter.prevent="$event.currentTarget.form.requestSubmit()"
            @keydown.meta.enter.prevent="$event.currentTarget.form.requestSubmit()"></textarea>
          <div class="workspace-summary">
            <button type="button" class="secondary compact-button" :disabled="!executorReady || busy || pickingWorkspace" @click="chooseWorkspace">
              {{ pickingWorkspace ? "Aguardando seleção…" : "Selecionar pasta" }}
            </button>
            <p v-if="selectedWorkspace" class="workspace-path">{{ selectedWorkspace.path }}</p>
          </div>
          <div class="run-actions">
            <button id="execute" type="submit" :disabled="!executorReady || busy || pickingWorkspace || !selectedWorkspace || !prompt.trim()" aria-keyshortcuts="Control+Enter Meta+Enter">
              {{ busy ? "Enviando…" : "Executar" }}
            </button>
            <kbd>Ctrl/⌘ + Enter</kbd>
          </div>
          <p v-if="workspaceError" class="composer-status" role="alert">{{ workspaceError }}</p>
          <p v-if="composerError" class="composer-status" role="alert">Falha ao iniciar a execução. {{ composerError }}</p>
        </form>
      </section>
      <p v-else-if="view === 'loading'" class="pane-notice" role="status">Carregando conversa…</p>

      <template v-else-if="view === 'thread'">
        <header class="conversation-heading">
          <div class="conversation-title">
            <h1 ref="conversationHeading" tabindex="-1">{{ conversation.title }}</h1>
            <p v-if="conversation.workspace" class="conversation-workspace" :title="conversation.workspace">
              {{ workspaceName(conversation.workspace) }}
            </p>
          </div>
          <div v-if="activeTurn" class="conversation-shortcuts">
            <button type="button" class="secondary compact-button active-shortcut"
              :aria-current="inspectedRunId === activeTurn.run_id ? 'true' : undefined" @click="inspectActive">
              <span class="turn-state-dot" aria-hidden="true"></span> Turno {{ activeNumber }} em andamento
            </button>
          </div>
          <div v-if="connection === CONNECTION.reconnecting || connection === CONNECTION.interrupted" class="connection-notice">
            <p role="status">{{ connection === CONNECTION.reconnecting ? "Reconectando ao turno ativo…" : "A conexão com a execução foi interrompida." }}</p>
            <button v-if="activeRunId" type="button" class="secondary" @click="resumeObservation(activeRunId)">Tentar agora</button>
          </div>
        </header>
        <ol ref="turnScroller" class="turn-list" aria-label="Turnos da conversa">
          <ConversationTurn v-for="(turn, index) in turns" :key="turn.run_id" :turn="presentedTurn(turn)" :number="index + 1"
            :selected="inspectedRunId === turn.run_id" :live="turn.run_id === activeRunId && connection === CONNECTION.receiving" :observed="turnEvidence(turn)"
            :workspace="observed.get(turn.run_id)?.workspace || (turn.run_id === activeRunId ? model.data.value.workspace : undefined)"
            :activity="turn.run_id === activeTurn?.run_id ? liveAction?.title || liveAction?.tool?.name || liveAction?.commandText : undefined"
            @inspect="inspectTurn" />
        </ol>
        <div class="conversation-composer">
          <form class="continuation-form" :class="{ 'continuation-waiting': !canContinue && !continuationDraft }" @submit.prevent="sendPrompt(continuationDraft, conversation.latest_run_id)">
            <label for="continuation-prompt">Continuar conversa</label>
            <textarea v-if="canContinue || continuationDraft" id="continuation-prompt" v-model="continuationDraft" rows="2" required
              :readonly="!canContinue" :aria-describedby="canContinue ? undefined : 'continuation-note'"
              placeholder="Envie um novo pedido nesta conversa."
              @keydown.ctrl.enter.prevent="$event.currentTarget.form.requestSubmit()"
              @keydown.meta.enter.prevent="$event.currentTarget.form.requestSubmit()"></textarea>
            <textarea v-else id="continuation-prompt" class="visually-hidden" disabled aria-describedby="continuation-note"></textarea>
            <p v-if="!canContinue" id="continuation-note" class="quiet-text">{{ continuationNote }}</p>
            <button type="submit" :disabled="!canContinue || !continuationDraft.trim()" aria-keyshortcuts="Control+Enter Meta+Enter">Enviar</button>
          </form>
          <p v-if="continuationError" role="alert" class="continuation-error">{{ continuationError }}</p>
        </div>
      </template>
    </main>

    <div v-if="hasPane" class="trajectory-region" :inert="drawer">
      <TrajectoryPane v-if="isCurrentPane" v-bind="paneProps" :model="model"
        :connection="connection"
        :last-update-seconds="lastUpdateSeconds" :recovered="recovered" :loading="liveLoading"
        :detail="outcome?.detail || (liveTurn?.status === 'failed' ? model.data.value.issue : undefined)"
        closeable
        @close="closeInspection" @active-turn="inspectActive" @call="changeActionCall" @retry="resumeObservation(activeRunId)"
        @scroll="rememberScroll" @announce="announce" />
      <HistoricalTrajectory v-else v-bind="paneProps" @observed="rememberEvidence" @active-turn="inspectActive"
        @close="closeInspection" @call="changeActionCall" @scroll="rememberScroll" @announce="announce"
        @retry="resumeObservation(activeRunId)" />
    </div>
  </div>
</template>
