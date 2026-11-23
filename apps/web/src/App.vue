<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, shallowReactive, shallowRef } from "vue";
import ActionDetails from "./ActionDetails.vue";
import ActionRow from "./ActionRow.vue";
import EventRow from "./EventRow.vue";
import ReplyHighlight from "./ReplyHighlight.vue";
import TechnicalEvents from "./TechnicalEvents.vue";
import {
  commandTitle, describeEvent, describeTool, errorMessage, firstLine, formatDuration, formatOffset,
  reportedTokens, statusPresentation, tokenSummary, trajectoryRows,
} from "./eventPresentation.js";
import { loadRunHistory, RunHttpError, streamPrompt, streamReplay, streamSavedRun } from "./runs.js";
import { CommandTimeline } from "./timeline.js";

const prompt = ref("");
const promptInput = ref(null);
const runHeader = ref(null);
const promptEditorOpen = ref(true);
const submittedPrompt = ref(null);
const timeline = ref(null);
const trajectoryBar = ref(null);
const backToTimelineButton = ref(null);
const returnPosition = shallowRef(null);
const detailOpen = computed(() => returnPosition.value !== null);
const entries = shallowReactive(new Map());
const rows = computed(() => [...entries.values()]);
const trajectoryView = ref("essential");
const failedOnly = ref(false);
const visibleRows = computed(() => trajectoryRows(rows.value, failedOnly.value ? "failures" : trajectoryView.value));
const failureCount = computed(() => rows.value.filter((entry) => entry.failed).length);
const actions = computed(() => rows.value.filter((entry) => entry.command || entry.tool));
const unconfirmedCount = computed(() => actions.value.filter((entry) =>
  (entry.command ?? entry.tool).status === "result_unconfirmed").length);
const activeAction = computed(() => actions.value.findLast((entry) =>
  (entry.command ?? entry.tool).status === "in_progress"));
const newEvents = ref(0);
const following = ref(false);
const activeRunId = ref(null);
const historyBefore = ref(null);
const loadingHistory = ref(false);
const historyError = ref("");
const seenRecordIds = new Set();
const busy = ref(false);
const replayMode = ref(false);
const runStatus = ref("Aguardando execução");
const connectionStatus = ref("Inativa");
const connectionLabel = computed(() => {
  if (replayMode.value) return "Replay";
  return ({
    "Recebendo eventos": "Ao vivo",
    "Reconectando": "Reconectando…",
    "Fluxo encerrado": "Encerrado",
  })[connectionStatus.value] ?? connectionStatus.value;
});
const resultFailure = ref(false);
const resultDetail = ref(null);
const turnIssue = ref(null);
const streamIssue = ref(null);
const failureReason = computed(() => resultDetail.value
  ?? (resultFailure.value ? turnIssue.value ?? streamIssue.value : null));
const tokens = shallowRef([]);
const reportedTotal = computed(() => tokenSummary(tokens.value));
const captureEnded = ref(false);
const lastReply = shallowRef(null);
const highlightedReply = computed(() => captureEnded.value ? lastReply.value : null);
const desktopQuery = window.matchMedia("(min-width: 1100px)");
const desktop = ref(desktopQuery.matches);
const selectedActionKey = ref(null);
const selectedAction = computed(() => entries.get(selectedActionKey.value));
const selectedTitle = computed(() => selectedAction.value ? actionTitle(selectedAction.value) : "");
const selectionPending = computed(() => Boolean(selectedActionKey.value && !selectedAction.value));
let controller;
let historyController;
let captureRecords = [];
let nextRecordKey = 0;
let commandTimeline = new CommandTimeline();
let lastScrollY = window.scrollY;
let refreshScheduled = false;
let replayOperation;

function commandKey(id) {
  return `command:${id}`;
}

function actionTitle(entry) {
  return entry.title || entry.tool?.name || entry.command?.command || entry.label;
}

function renderCommand(command) {
  const key = commandKey(command.id);
  const status = statusPresentation(command.status);
  entries.set(key, {
    key, command, title: commandTitle(command.command), ...status,
    preview: status.failed ? firstLine(command.output) : null,
    duration: formatDuration(command.observedDurationNs),
    offset: formatOffset(command.records[0].elapsed_ns),
    startedOffset: formatOffset(command.startedOffsetNs),
    completedOffset: formatOffset(command.completedOffsetNs),
  });
}

function acceptRecord(record, eventId, commands) {
  if (eventId != null) {
    const deliveryId = String(eventId);
    if (seenRecordIds.has(deliveryId)) return null;
    seenRecordIds.add(deliveryId);
  }

  const itemId = record.event.item?.id;
  return {
    key: eventId == null ? `record:${nextRecordKey++}` : `delivery:${eventId}`,
    record,
    duration: commands && Object.hasOwn(commands, itemId) ? commands[itemId].observed_duration_ns : null,
  };
}

function projectRecord({ key, record, duration }) {
  const { event } = record;
  if (event.type === "turn.started") {
    turnIssue.value = null;
    streamIssue.value = null;
    tokens.value = [];
    lastReply.value = null;
  }
  if (event.type === "turn.failed") turnIssue.value = errorMessage(event);
  if (event.type === "error") streamIssue.value = errorMessage(event);
  if (event.type === "turn.completed") tokens.value = reportedTokens(event);

  const command = commandTimeline.observe(record, duration);
  if (command) {
    renderCommand(command);
    return;
  }

  const item = event.item;
  const toolKey = typeof item?.id === "string" ? JSON.stringify([item.type, item.id]) : key;
  const previous = entries.get(toolKey);
  const tool = describeTool(record, previous?.tool);
  if (tool) {
    const records = [...(previous?.records ?? []), record];
    entries.set(toolKey, {
      key: toolKey, tool, records, event, ...statusPresentation(tool.status),
      offset: formatOffset(records[0].elapsed_ns),
    });
    return;
  }
  const entry = { key, event, ...describeEvent(event), offset: formatOffset(record.elapsed_ns) };
  entries.set(key, entry);
  if (event.type === "item.completed" && entry.markdown?.trim()) lastReply.value = entry;
}

function appendRecord(record, eventId, { live = false, commands = null } = {}) {
  const accepted = acceptRecord(record, eventId, commands);
  if (!accepted) return false;
  captureRecords.push(accepted);
  projectRecord(accepted);
  if (live) {
    if (!following.value) newEvents.value++;
    updateLivePosition();
  }
  return true;
}

function finishActions() {
  captureEnded.value = true;
  for (const command of commandTimeline.finish()) renderCommand(command);
  for (const [key, entry] of entries) {
    if (entry.tool && !entry.tool.terminal) {
      const status = "result_unconfirmed";
      entries.set(key, { ...entry, tool: { ...entry.tool, status }, ...statusPresentation(status) });
    }
  }
}

function atLatestEvent() {
  const lastRow = timeline.value?.lastElementChild;
  return !lastRow || lastRow.getBoundingClientRect().bottom <= window.innerHeight + 24;
}

function markVisibleEvents() {
  if (following.value && window.scrollY < lastScrollY && !atLatestEvent()) following.value = false;
  lastScrollY = window.scrollY;
  if (!detailOpen.value && !failedOnly.value && atLatestEvent()) newEvents.value = 0;
}

async function updateLivePosition() {
  if (refreshScheduled) return;
  refreshScheduled = true;
  await nextTick();
  refreshScheduled = false;
  if (following.value && window.scrollY < lastScrollY && !atLatestEvent()) following.value = false;
  if (following.value) timeline.value?.lastElementChild?.scrollIntoView({ block: "end" });
  lastScrollY = window.scrollY;
  if (!detailOpen.value && !failedOnly.value && atLatestEvent()) newEvents.value = 0;
}

async function toggleFollowing() {
  if (following.value) {
    following.value = false;
    return;
  }
  failedOnly.value = false;
  following.value = busy.value;
  await nextTick();
  timeline.value?.lastElementChild?.scrollIntoView({ block: "end" });
  lastScrollY = window.scrollY;
  newEvents.value = 0;
}

function keepFocusedEventVisible(event) {
  const toolbar = trajectoryBar.value?.getBoundingClientRect();
  const target = event.target.getBoundingClientRect();
  if (toolbar && target.top < toolbar.bottom) {
    window.scrollBy(0, target.top - toolbar.bottom - 4);
  }
}

function selectionStorageKey(runId) {
  return `agent-lens:action:${runId}`;
}

function rememberSelection(entry) {
  if (!activeRunId.value) return;
  try {
    const key = selectionStorageKey(activeRunId.value);
    if (entry.command || (entry.tool && typeof entry.event?.item?.id === "string") || entry.key.startsWith("delivery:")) {
      sessionStorage.setItem(key, entry.key);
    }
    else sessionStorage.removeItem(key);
  } catch {
    // The inspector still works if browser storage is unavailable.
  }
}

function restoreSelection(runId) {
  try {
    selectedActionKey.value = sessionStorage.getItem(selectionStorageKey(runId));
  } catch {
    // A blocked browser store does not prevent restoring the run.
  }
}

async function jumpToAction(entry) {
  following.value = false;
  if (!entry.failed) failedOnly.value = false;
  await nextTick();
  const row = document.getElementById(`timeline-${entry.key}`);
  const trigger = row?.querySelector("button, summary");
  if (!trigger) return;
  row.scrollIntoView({ block: "center" });
  trigger.focus({ preventScroll: true });
  selectAction(entry, trigger);
}

async function jumpToReply(key) {
  following.value = false;
  failedOnly.value = false;
  await nextTick();
  const row = document.getElementById(`timeline-${key}`);
  if (!row) return;
  row.scrollIntoView({ block: "center" });
  row.focus({ preventScroll: true });
}

function jumpToFirstFailure() {
  const entry = rows.value.find((row) => row.failed);
  if (entry) return jumpToAction(entry);
}

async function loadEarlierEvents() {
  if (loadingHistory.value || historyBefore.value == null || !activeRunId.value) return;
  following.value = false;
  const request = new AbortController();
  historyController = request;
  loadingHistory.value = true;
  historyError.value = "";

  try {
    const page = await loadRunHistory(activeRunId.value, request.signal, historyBefore.value);
    if (request.signal.aborted) return;
    const threshold = trajectoryBar.value?.getBoundingClientRect().bottom ?? 0;
    const anchor = Array.from(timeline.value?.children ?? []).find((row) => row.getBoundingClientRect().bottom > threshold);
    const top = anchor?.getBoundingClientRect().top;
    const technicalKey = anchor?.dataset.technicalKey;
    const wasExpanded = Boolean(technicalKey && anchor.firstElementChild.open);
    const focused = document.activeElement;
    const earlier = page.events.map(({ record, event_id: id }) => acceptRecord(record, id, page.commands)).filter(Boolean);
    captureRecords = [...earlier, ...captureRecords];
    historyBefore.value = page.next_before;
    entries.clear();
    lastReply.value = null;
    commandTimeline = new CommandTimeline();
    for (const record of captureRecords) projectRecord(record);
    if (captureEnded.value) finishActions();
    await nextTick();
    let restoredAnchor = anchor;
    if (anchor && !anchor.isConnected && technicalKey) {
      const group = visibleRows.value.find((entry) => entry.technicalEvents?.some(({ key }) => key === technicalKey));
      if (group) {
        restoredAnchor = document.getElementById(`timeline-${group.key}`);
        if (restoredAnchor && wasExpanded) restoredAnchor.firstElementChild.open = true;
      }
    }
    if (restoredAnchor?.isConnected) {
      window.scrollBy(0, restoredAnchor.getBoundingClientRect().top - top);
      if (!focused?.isConnected) {
        restoredAnchor.querySelector("button, summary")?.focus({ preventScroll: true });
      }
    }
  } catch {
    if (!request.signal.aborted) historyError.value = "Falha ao carregar eventos anteriores. Tente novamente.";
  } finally {
    if (historyController === request) {
      loadingHistory.value = false;
      historyController = undefined;
    }
  }
}

function updateDesktop(event) {
  desktop.value = event.matches;
  if (event.matches) returnToTimeline();
}

async function selectAction(entry, trigger) {
  following.value = false;
  selectedActionKey.value = entry.key;
  rememberSelection(entry);
  if (desktop.value) return;

  returnPosition.value = {
    trigger,
    scrollY: window.scrollY,
    triggerTop: trigger.getBoundingClientRect().top,
  };
  await nextTick();
  window.scrollTo(0, 0);
  backToTimelineButton.value?.focus({ preventScroll: true });
}

async function returnToTimeline() {
  if (!returnPosition.value) return;
  const { trigger, scrollY, triggerTop } = returnPosition.value;
  returnPosition.value = null;
  await nextTick();

  if (trigger.isConnected) {
    // Earlier rows may have changed height while the trajectory was hidden.
    const shift = trigger.getBoundingClientRect().top - triggerTop;
    window.scrollTo(0, window.scrollY + shift);
    trigger.focus({ preventScroll: true });
  } else {
    window.scrollTo(0, scrollY);
  }
}

function saveRunId(runId) {
  activeRunId.value = runId;
  const url = new URL(window.location.href);
  if (runId) url.searchParams.set("run_id", runId);
  else url.searchParams.delete("run_id");
  window.history.replaceState(null, "", url);
}

function observedTurn(previous, { event }) {
  const type = event.type;
  return ["turn.started", "turn.completed", "turn.failed"].includes(type) ? type : previous;
}

function runResultLabel(exitCode, error, turn) {
  if (error === "event_too_large") {
    return "Captura incompleta. Um evento excedeu o limite de 8 MiB.";
  }
  if (turn === "turn.failed" || (Number.isInteger(exitCode) && exitCode !== 0)) {
    const code = Number.isInteger(exitCode) ? ` (código ${exitCode})` : "";
    return `Falhou${code}`;
  }
  if (exitCode === 0 && turn === "turn.completed") return "Concluída (código 0)";
  return "Resultado não confirmado";
}

function showRunResult({ exit_code: exitCode, error }, turn) {
  if (error !== "capture_unavailable") finishActions();
  resultFailure.value = turn === "turn.failed" || (Number.isInteger(exitCode) && exitCode !== 0);
  if (error === "capture_unavailable") resultDetail.value = "A captura ficou indisponível.";
  if (error === "execution_failed") resultDetail.value = "O executor encerrou sem informar um código de saída.";
  runStatus.value = runResultLabel(exitCode, error, turn);
  connectionStatus.value = error === "capture_unavailable" ? "Interrompida" : "Fluxo encerrado";
}

async function display({ run, connection }, operation, formatErrorLabel) {
  if (busy.value) return;
  controller = new AbortController();
  const { signal } = controller;
  busy.value = true;
  historyController?.abort();
  historyController = undefined;
  loadingHistory.value = false;
  historyBefore.value = null;
  historyError.value = "";
  captureRecords = [];
  captureEnded.value = false;
  lastReply.value = null;
  nextRecordKey = 0;
  entries.clear();
  seenRecordIds.clear();
  commandTimeline = new CommandTimeline();
  selectedActionKey.value = null;
  returnPosition.value = null;
  trajectoryView.value = "essential";
  failedOnly.value = false;
  following.value = false;
  newEvents.value = 0;
  resultFailure.value = false;
  resultDetail.value = null;
  turnIssue.value = null;
  streamIssue.value = null;
  tokens.value = [];
  runStatus.value = run;
  connectionStatus.value = connection;

  try {
    await operation(signal);
  } catch (error) {
    if (!signal.aborted) {
      runStatus.value = formatErrorLabel(error);
      connectionStatus.value = "Interrompida";
      if (error instanceof RunHttpError && error.status !== 404) resultDetail.value = error.detail;
    }
  } finally {
    controller = undefined;
    busy.value = false;
    following.value = false;
  }
}

async function openPromptEditor() {
  promptEditorOpen.value = true;
  await nextTick();
  promptInput.value?.focus();
}

async function showRunHeader() {
  promptEditorOpen.value = false;
  const focused = document.activeElement;
  if (focused?.closest(".run-composer")) {
    await nextTick();
    runHeader.value?.focus({ preventScroll: true });
  }
}

function execute() {
  if (busy.value) return;
  if (!prompt.value.trim()) {
    runStatus.value = "Digite um prompt para executar.";
    promptInput.value.focus();
    return;
  }

  const sent = prompt.value;
  saveRunId(null);
  replayMode.value = false;
  return display(
    { run: "Iniciando", connection: "Enviando prompt" },
    async (signal) => {
      let turn;
      const result = await streamPrompt(sent, {
        signal,
        onAccepted(runId) {
          saveRunId(runId);
          submittedPrompt.value = sent;
          showRunHeader();
          runStatus.value = "Em andamento";
          connectionStatus.value = "Recebendo eventos";
        },
        onRecord(record) {
          appendRecord(record, null, { live: true });
          turn = observedTurn(turn, record);
        },
      });
      if (result) showRunResult(result, turn);
      else {
        runStatus.value = "Resultado não confirmado";
        connectionStatus.value = "Interrompida";
      }
    },
    (error) => {
      if (error instanceof RunHttpError) {
        resultFailure.value = true;
        resultDetail.value = error.detail;
        return `Falha ao iniciar a execução (HTTP ${error.status})`;
      }
      return "Resultado não confirmado";
    },
  );
}

async function replay() {
  if (busy.value && !replayOperation) return;
  if (busy.value) {
    controller?.abort();
    await replayOperation;
    if (busy.value) return;
  }
  saveRunId(null);
  replayMode.value = true;
  submittedPrompt.value = null;
  showRunHeader();
  const operation = display(
    { run: "Replay em andamento", connection: "Conectando ao replay" },
    async (signal) => {
      await streamReplay({
        signal,
        onOpen: () => { connectionStatus.value = "Recebendo eventos do replay"; },
        onRecord: (record, id) => appendRecord(record, id, { live: true }),
      });
      finishActions();
      runStatus.value = "Replay concluído";
      connectionStatus.value = "Fluxo encerrado";
    },
    () => "Replay incompleto",
  );
  replayOperation = operation;
  await operation;
  if (replayOperation === operation) replayOperation = undefined;
}

function reopen(runId) {
  if (busy.value) return;
  activeRunId.value = runId;
  replayMode.value = false;
  showRunHeader();
  return display(
    { run: "Restaurando execução", connection: "Carregando histórico" },
    async (signal) => {
      const page = await loadRunHistory(runId, signal);
      for (const { event_id: eventId, record } of page.events) appendRecord(record, eventId, { commands: page.commands });
      historyBefore.value = page.next_before;
      restoreSelection(runId);
      resultFailure.value = page.status === "failed";
      runStatus.value = page.status === "completed" || page.status === "failed"
        ? runResultLabel(page.exit_code, null, page.last_turn)
        : "Aguardando resultado";

      let turn = page.last_turn;
      const result = await streamSavedRun(runId, page.latest_event_id ?? 0, {
        signal,
        onOpen: () => { connectionStatus.value = "Recebendo eventos"; },
        onRecord(record, eventId) {
          if (appendRecord(record, eventId, { live: true })) turn = observedTurn(turn, record);
        },
        onReconnect: () => { connectionStatus.value = "Reconectando"; },
      });
      showRunResult(result, turn);
    },
    (error) => {
      if (error instanceof RunHttpError && error.status === 404) {
        promptEditorOpen.value = true;
        return "Execução salva não encontrada";
      }
      return "Falha ao restaurar ou acompanhar a execução";
    },
  );
}

onMounted(() => {
  desktopQuery.addEventListener("change", updateDesktop);
  window.addEventListener("scroll", markVisibleEvents, { passive: true });
  const runId = new URLSearchParams(window.location.search).get("run_id");
  if (runId) reopen(runId);
});

onUnmounted(() => {
  desktopQuery.removeEventListener("change", updateDesktop);
  window.removeEventListener("scroll", markVisibleEvents);
  controller?.abort();
  historyController?.abort();
});
</script>

<template>
  <header class="topbar">
    <div class="topbar-content">
      <span class="wordmark">Agent Lens</span>
      <div class="topbar-context">
        <span class="workspace">Workspace de exemplo · Somente leitura</span>
        <div v-if="!promptEditorOpen && !detailOpen" class="topbar-actions">
          <button class="secondary" type="button" :disabled="busy" @click="openPromptEditor">Nova execução</button>
          <button v-if="!replayMode" class="link-button" type="button" :disabled="busy" @click="replay">Ver replay de exemplo</button>
        </div>
      </div>
    </div>
  </header>

  <main class="content" :class="{ 'composer-content': promptEditorOpen && !detailOpen }">
    <div v-show="!detailOpen">
      <section v-if="promptEditorOpen" class="run-composer" aria-labelledby="composer-title">
        <h1 id="composer-title">Veja o que o agente faz entre o pedido e a resposta</h1>
        <form class="run-form" id="run-form" @submit.prevent="execute">
          <label for="prompt">O que o Codex deve fazer?</label>
          <textarea
            id="prompt"
            ref="promptInput"
            v-model="prompt"
            name="prompt"
            rows="4"
            required
            :readonly="busy"
            placeholder="Leia o README e identifique como iniciar o serviço."
            @keydown.ctrl.enter.prevent="$event.currentTarget.form.requestSubmit()"
            @keydown.meta.enter.prevent="$event.currentTarget.form.requestSubmit()"
          ></textarea>
          <div class="workspace-summary">
            <p><span>Workspace</span><strong>Workspace de exemplo</strong></p>
            <span class="quiet-label">Somente leitura</span>
          </div>
          <div class="run-actions">
            <button id="execute" type="submit" :disabled="busy" aria-keyshortcuts="Control+Enter Meta+Enter">Executar</button>
            <kbd>Ctrl/⌘ + Enter</kbd>
          </div>
        </form>
        <div class="replay-banner">
          <p><strong>Ver um replay de exemplo</strong><span>Eventos gravados e sanitizados. Sem chamada de modelo.</span></p>
          <button id="replay" class="secondary" type="button" :disabled="busy" @click="replay">Ver replay de exemplo</button>
        </div>
      </section>
      <header v-else ref="runHeader" class="run-summary" tabindex="-1">
        <div class="run-heading">
          <div class="run-identity">
            <p class="run-kicker">Codex CLI <template v-if="activeRunId"> · <code class="run-id">{{ activeRunId }}</code></template></p>
            <h1>Execução do agente</h1>
          </div>
          <div class="run-statuses" role="status">
            <span id="run-status" class="state-label" :class="{ 'state-failed': resultFailure }">
              <span aria-hidden="true">{{ resultFailure ? "×" : captureEnded ? "✓" : "●" }}</span>{{ runStatus }}
            </span>
            <span id="connection-status" class="state-label connection-state" :title="connectionStatus">
              {{ connectionLabel }}
            </span>
            <button v-if="failureCount" type="button" class="state-label state-failed" @click="jumpToFirstFailure">
              <span aria-hidden="true">×</span>{{ failureCount }} {{ failureCount === 1 ? "falha" : "falhas" }}
            </button>
            <span v-if="unconfirmedCount" class="state-label">{{ unconfirmedCount }} {{ unconfirmedCount === 1 ? "não confirmada" : "não confirmadas" }}</span>
          </div>
        </div>
        <div v-if="connectionStatus === 'Reconectando' || connectionStatus === 'Interrompida'" class="connection-notice" role="status">
          <p>{{ connectionStatus === "Reconectando"
            ? "Conexão perdida. Tentando reconectar. Os estados podem estar desatualizados."
            : "Fluxo interrompido. Os eventos exibidos permanecem disponíveis." }}</p>
          <button v-if="activeRunId && !busy" class="secondary" type="button" @click="reopen(activeRunId)">Reconectar</button>
        </div>
        <div v-if="replayMode" class="replay-banner">
          <p>Replay de eventos gravados. Nenhum modelo está sendo chamado.</p>
          <button id="replay" class="secondary" type="button" aria-label="Reiniciar replay" @click="replay">Reiniciar</button>
        </div>
        <p v-if="failureReason" class="run-failure" role="status">{{ failureReason }}</p>
        <details v-if="submittedPrompt" class="prompt-summary">
          <summary>
            <strong>Prompt</strong>
            <span class="prompt-preview">{{ submittedPrompt }}</span>
            <span class="prompt-toggle" aria-hidden="true"><span>Expandir ▾</span><span>Recolher ▴</span></span>
          </summary>
          <p>{{ submittedPrompt }}</p>
        </details>
        <div v-if="actions.length || reportedTotal || failureCount" class="run-stats">
          <p v-if="reportedTotal"><strong>{{ reportedTotal.value }}</strong> {{ reportedTotal.label }}</p>
          <p v-if="actions.length">
            <strong>{{ actions.length }}</strong> {{ actions.length === 1 ? "ação" : "ações" }}
            <template v-if="historyBefore != null"> {{ actions.length === 1 ? "carregada" : "carregadas" }}</template>
            <span v-if="busy && !captureEnded && activeAction" class="run-active-action"> · Em andamento <strong>{{ actionTitle(activeAction) }}</strong></span>
          </p>
          <p v-if="failureCount"><strong>{{ failureCount }}</strong> {{ failureCount === 1 ? "falha" : "falhas" }}</p>
        </div>
      </header>
      <p v-if="promptEditorOpen && (busy || resultFailure || runStatus === 'Execução salva não encontrada')" id="run-status" class="composer-status" role="status">
        {{ runStatus }}<template v-if="failureReason">. {{ failureReason }}</template>
      </p>
      <ReplyHighlight
        v-if="!promptEditorOpen && highlightedReply"
        :key="highlightedReply.key"
        :entry="highlightedReply"
        @jump="jumpToReply"
      />
      <div v-if="!promptEditorOpen && (rows.length || historyBefore != null)" class="run-layout">
        <div class="run-sidebar">
          <aside
            v-if="desktop"
            id="action-inspector"
            class="command-inspector"
            aria-label="Inspetor de ações"
            :data-command-status="(selectedAction?.command ?? selectedAction?.tool)?.status"
          >
            <div v-if="selectedAction" class="inspector-heading">
              <span class="run-kicker">{{ selectedAction.command ? "Comando" : selectedAction.tool ? "Ferramenta" : "Evento" }}</span>
              <p v-if="selectedAction.command || selectedAction.tool || selectedAction.failed" class="command-status">
                <span class="status-symbol" aria-hidden="true">{{ selectedAction.icon }}</span>
                {{ selectedAction.icon ? selectedAction.label : selectedAction.failed ? "Falhou" : selectedAction.label }}
              </p>
              <h2>{{ selectedTitle }}</h2>
            </div>
            <ActionDetails v-if="selectedAction" :key="selectedAction.key" :entry="selectedAction" />
            <section v-else class="run-overview">
              <h2>Resumo da execução</h2>
              <dl>
                <div><dt>Falhas</dt><dd>{{ failureCount }}</dd></div>
                <div><dt>Não confirmadas</dt><dd>{{ unconfirmedCount }}</dd></div>
              </dl>
              <template v-if="tokens.length">
                <h3>Tokens reportados pelo Codex</h3>
                <dl>
                  <div v-for="token in tokens" :key="token.field">
                    <dt>{{ token.label }}</dt><dd>{{ token.value }}</dd>
                  </div>
                </dl>
              </template>
            </section>
          </aside>
          <details v-if="actions.length" class="action-index" :open="desktop">
            <summary>Índice de ações <span>{{ actions.length }}{{ historyBefore != null ? " carregadas" : "" }}</span></summary>
            <nav aria-label="Índice de ações da trajetória">
              <ol>
                <li v-for="entry in actions" :key="entry.key" :data-failure="entry.failed || undefined">
                  <button
                    class="index-action"
                    type="button"
                    :aria-current="selectedActionKey === entry.key ? 'true' : undefined"
                    @click="jumpToAction(entry)"
                  >
                    <span class="status-symbol" aria-hidden="true">{{ entry.icon }}</span>
                    <span class="visually-hidden">{{ entry.label }}</span>
                    <span v-if="entry.title || entry.tool" class="index-title">{{ actionTitle(entry) }}</span>
                    <code v-else>{{ entry.command?.command || entry.tool?.name || "Comando" }}</code>
                  </button>
                </li>
              </ol>
            </nav>
          </details>

        </div>

        <section class="events" aria-labelledby="events-title">
          <div ref="trajectoryBar" class="events-heading">
            <h2 id="events-title">Trajetória</h2>
            <div v-if="rows.length" class="trajectory-controls">
              <div class="event-filters" aria-label="Filtrar eventos" role="group">
                <button type="button" :aria-pressed="trajectoryView === 'essential'" @click="trajectoryView = 'essential'; following = false">Essencial</button>
                <button type="button" :aria-pressed="trajectoryView === 'all'" @click="trajectoryView = 'all'; following = false">Tudo</button>
                <button type="button" :aria-pressed="failedOnly" @click="failedOnly = !failedOnly; following = false">
                  Falhas <span v-if="failureCount" class="failure-count">{{ failureCount }}</span>
                </button>
              </div>
            </div>
          </div>
          <div v-if="historyBefore != null" class="history-controls">
            <button
              type="button"
              class="link-button"
              :aria-disabled="loadingHistory"
              @click="loadEarlierEvents"
            >{{ loadingHistory ? "Carregando eventos anteriores…" : "Carregar eventos anteriores" }}</button>
            <p v-if="historyError" role="status">{{ historyError }}</p>
          </div>
          <p v-if="selectionPending" class="history-controls" role="status">
            {{ historyBefore != null ? "A ação selecionada ainda não está nos eventos carregados." : "A ação selecionada não está no histórico disponível." }}
          </p>
          <p v-if="failedOnly && !visibleRows.length" class="empty-timeline">
            Nenhuma falha nos eventos carregados.
          </p>
          <ol id="timeline" ref="timeline" class="event-list" @focusin="keepFocusedEventVisible">
            <li
              v-for="entry in visibleRows"
              :id="`timeline-${entry.key}`"
              :key="entry.key"
              v-memo="[entry, desktop, entry.technicalEvents ? selectedActionKey : selectedActionKey === entry.key]"
              :tabindex="entry.markdown ? -1 : undefined"
              :data-technical-key="entry.technicalEvents?.[0].key"
              :data-event-type="entry.event?.type"
              :data-command-id="entry.command?.id"
              :data-command-status="(entry.command ?? entry.tool)?.status"
              :class="{ 'trajectory-action': entry.command || entry.tool, 'trajectory-message': entry.markdown }"
              :data-failure="entry.failed || undefined"
            >
              <ActionRow
                v-if="entry.command || entry.tool"
                :entry="entry"
                :desktop="desktop"
                :selected="selectedActionKey === entry.key"
                @select="selectAction"
              />
              <TechnicalEvents v-else-if="entry.technicalEvents" :events="entry.technicalEvents" :selected-key="selectedActionKey" @select="selectAction" />
              <EventRow v-else :entry="entry" :selected="selectedActionKey === entry.key" @select="selectAction" />
            </li>
          </ol>
          <div v-if="newEvents && !following && !failedOnly" class="new-events">
            <button type="button" class="secondary" @click="toggleFollowing">
              <span aria-hidden="true">↓</span> {{ newEvents === 1 ? "1 novo evento" : `${newEvents} novos eventos` }}
            </button>
          </div>
        </section>
      </div>
    </div>

    <section
      v-if="detailOpen && selectedAction"
      class="narrow-command-detail"
      aria-labelledby="command-detail-title"
      @keydown.esc.prevent="returnToTimeline"
    >
      <button
        ref="backToTimelineButton"
        type="button"
        class="link-button detail-back"
        @click="returnToTimeline"
      >
        <span aria-hidden="true">←</span> Voltar à trajetória
      </button>
      <div class="narrow-command-card" :data-command-status="(selectedAction.command ?? selectedAction.tool)?.status">
        <header class="narrow-command-heading">
          <span class="run-kicker">{{ selectedAction.command ? "Comando" : selectedAction.tool ? "Ferramenta" : "Evento" }}</span>
          <p v-if="selectedAction.command || selectedAction.tool" class="command-status">
            <span class="status-symbol" aria-hidden="true">{{ selectedAction.icon }}</span>
            {{ selectedAction.label }}
          </p>
          <h1 id="command-detail-title">{{ selectedTitle }}</h1>
        </header>
        <ActionDetails :key="selectedAction.key" :entry="selectedAction" />
      </div>
    </section>
  </main>
</template>
