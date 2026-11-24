<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { formatDuration, maskPaths, observedRunDuration, reportedTokens, tokenSummary, trajectoryRows } from "./eventPresentation.js";
import { actionCallId, observedFileChanges } from "./trajectoryProjection.js";
import TechnicalEvents from "./TechnicalEvents.vue";
import TrajectoryEntry from "./TrajectoryEntry.vue";

const props = defineProps({
  model: { type: Object, required: true },
  runId: String,
  summary: Object,
  turnNumber: Number,
  prompt: String,
  status: String,
  detail: String,
  active: Boolean,
  activeTurnNumber: Number,
  activeConnection: String,
  connection: String,
  lastUpdateSeconds: Number,
  recovered: Number,
  loading: Boolean,
  error: String,
  linkedCall: String,
  initialScroll: { type: Number, default: 0 },
  focusOnOpen: Boolean,
  replay: Boolean,
  closeable: Boolean,
});
const emit = defineEmits(["close", "active-turn", "call", "retry", "retry-history", "scroll", "announce", "replay"]);
const heading = ref(null);
const viewport = ref(null);
const timeline = ref(null);
const filter = ref("essential");
const selected = ref(null);
const manuallySelected = ref(false);
const following = ref(props.active && !props.linkedCall && props.initialScroll === 0);
const now = ref(0);
const lastArrival = ref(null);
let expectedScroll = null;
let initialized = false;
let mounted = false;
let resolvedCall;
let locatingCall;
const { data, nextBefore, loadEarlier, loadingEarlier, historyError } = props.model;

function isEssential(entry) {
  return !entry.startup && (entry.command || entry.tool || entry.failed
    || ["agent_message", "reasoning"].includes(entry.event?.item?.type));
}
const visible = computed(() => trajectoryRows(data.value.rows, filter.value)
  .filter(entry => filter.value !== "essential" || isEssential(entry)));
const tokenDetails = computed(() => props.summary
  ? reportedTokens({ type: "turn.completed", usage: props.summary.tokens }) : data.value.tokens);
const tokens = computed(() => tokenSummary(tokenDetails.value));
const actions = computed(() => props.summary ? props.summary.actions : data.value.actions.length);
const failures = computed(() => props.summary ? props.summary.failures : data.value.failures.length);
const partial = computed(() => props.summary ? props.summary.partial : nextBefore.value != null);
function showEvidence(itemId) {
  following.value = false;
  if (itemId === props.linkedCall) {
    const entry = linkedEntry(itemId);
    if (entry) jumpTo(entry);
  } else emit("call", itemId);
}
const files = computed(() => observedFileChanges(data.value.actions, data.value.workspace));
const expanded = computed(() => {
  if (props.linkedCall) return data.value.rows.find((entry) => actionCallId(entry) === props.linkedCall)?.key;
  if (manuallySelected.value || props.active || props.replay) return selected.value;
  return data.value.failures[0]?.key;
});
const duration = computed(() => {
  if (props.summary) return formatDuration(props.summary.observed_duration_ns);
  const liveElapsed = props.active && lastArrival.value !== null && props.connection === "Ao vivo"
    ? Math.max(0, now.value - lastArrival.value) : 0;
  return data.value.lastOffset > data.value.firstOffset
    ? observedRunDuration(data.value.firstOffset, data.value.lastOffset, liveElapsed) : null;
});
const switchToActive = computed(() => !props.active && props.activeTurnNumber != null);
const headingText = computed(() => props.replay ? "Replay de exemplo"
  : props.turnNumber != null ? `Trajetória · Turno ${props.turnNumber}` : "Trajetória");
const hasObservedActions = computed(() => props.summary ? actions.value != null : data.value.actions.length > 0
  || (data.value.terminal && data.value.rows.length > 0 && nextBefore.value == null));
const index = computed(() => data.value.actions.filter((entry) => !entry.startup || filter.value === "all"));

watch(() => props.active && props.connection === "Ao vivo", (active, _, onCleanup) => {
  if (!active) return;
  const timer = setInterval(() => { now.value = performance.now(); }, 250);
  onCleanup(() => clearInterval(timer));
}, { immediate: true });

function scrollTo(top) {
  const pane = viewport.value;
  if (!pane) return;
  pane.scrollTop = top;
  expectedScroll = pane.scrollTop;
}

function lastEvent() {
  const pane = viewport.value;
  const row = timeline.value?.lastElementChild;
  if (pane && row) {
    const bottom = row.getBoundingClientRect().bottom;
    scrollTo(pane.scrollTop + bottom - pane.getBoundingClientRect().bottom + 16);
  }
}

function onScroll() {
  const top = viewport.value.scrollTop;
  if (expectedScroll !== top) following.value = false;
  expectedScroll = null;
  if (!props.loading && !props.error) emit("scroll", top);
}

watch(data, async (current, previous) => {
  if (current.lastOffset !== previous.lastOffset) lastArrival.value = performance.now();
  if (!following.value) return;
  await nextTick();
  if (following.value) lastEvent();
});

function followLatest() {
  filter.value = filter.value === "failures" ? "essential" : filter.value;
  following.value = props.active;
  nextTick(lastEvent);
}

function select(entry) {
  following.value = false;
  manuallySelected.value = true;
  selected.value = expanded.value === entry.key ? null : entry.key;
  resolvedCall = selected.value ? actionCallId(entry) : undefined;
  emit("call", resolvedCall ?? null);
}

function followLinkTo(entry, focus = false) {
  const rows = timeline.value?.querySelectorAll("[data-entry-key]") ?? [];
  const row = [...rows].find((element) => element.dataset.entryKey === entry.key);
  if (!row) return;
  const group = row.closest("details.technical-events");
  if (group) group.open = true;
  const box = viewport.value.getBoundingClientRect();
  scrollTo(viewport.value.scrollTop + row.getBoundingClientRect().top - box.top - 10);
  if (focus) row.querySelector("button")?.focus({ preventScroll: true });
}

async function jumpTo(entry) {
  if (filter.value === "failures" && !entry.failed) filter.value = "essential";
  if (!isEssential(entry)) filter.value = "all";
  following.value = false;
  manuallySelected.value = true;
  selected.value = entry.key;
  resolvedCall = actionCallId(entry);
  emit("call", resolvedCall);
  await nextTick();
  followLinkTo(entry, true);
}

async function earlier() {
  if (!props.runId) return;
  following.value = false;
  const pane = viewport.value;
  const top = pane.getBoundingClientRect().top;
  const anchor = [...(timeline.value?.children ?? [])].find((row) => row.getBoundingClientRect().bottom > top);
  const previousTop = anchor?.getBoundingClientRect().top;
  await loadEarlier(props.runId);
  await nextTick();
  if (anchor?.isConnected) scrollTo(pane.scrollTop + anchor.getBoundingClientRect().top - previousTop);
}

function linkedEntry(call) {
  return data.value.rows.find((row) => actionCallId(row) === call);
}

async function locateAction(call) {
  if (!mounted || props.loading || !call || resolvedCall === call || locatingCall === call) return;
  locatingCall = call;
  try {
    let entry = linkedEntry(call);
    while (!entry && mounted && props.linkedCall === call && nextBefore.value != null
        && !loadingEarlier.value && !historyError.value) {
      const before = nextBefore.value;
      await loadEarlier(props.runId);
      if (nextBefore.value === before) break;
      entry = linkedEntry(call);
    }
    if (!entry || !mounted || props.linkedCall !== call) return;
    resolvedCall = call;
    following.value = false;
    if (!isEssential(entry)) filter.value = "all";
    else if (!entry.failed && filter.value === "failures") filter.value = "essential";
    await nextTick();
    if (mounted && props.linkedCall === call) followLinkTo(entry);
  } finally {
    if (locatingCall === call) locatingCall = undefined;
  }
}

watch(() => [props.linkedCall, props.loading, data.value.rows], ([call]) => locateAction(call), { flush: "post" });
watch(() => props.linkedCall, (call) => {
  if (!call) {
    resolvedCall = undefined;
    selected.value = null;
    manuallySelected.value = true;
  }
});

async function restoreScroll() {
  if (initialized || props.loading) return;
  initialized = true;
  await nextTick();
  if (props.linkedCall) {
    locateAction(props.linkedCall);
    return;
  }
  if (props.initialScroll > 0) scrollTo(props.initialScroll);
  else if (following.value) lastEvent();
}

onMounted(() => {
  mounted = true;
  if (props.focusOnOpen) heading.value?.focus({ preventScroll: true });
  restoreScroll();
});
watch(() => props.loading, restoreScroll);
onUnmounted(() => {
  mounted = false;
  if (locatingCall) props.model.cancelEarlier();
});
</script>

<template>
  <aside id="trajectory-pane" class="trajectory-pane" aria-labelledby="trajectory-title">
    <header class="trajectory-header">
      <button v-if="closeable" type="button" class="link-button trajectory-mobile-back" @click="$emit('close')">
        <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m14 6-6 6 6 6M8 12h12"/></svg> Conversa
      </button>
      <h2 id="trajectory-title" ref="heading" tabindex="-1"><span class="trajectory-heading-wide">{{ headingText }}</span><span v-if="turnNumber" class="trajectory-heading-narrow">Turno {{ turnNumber }}</span></h2>
      <button v-if="closeable" type="button" class="link-button trajectory-close" @click="$emit('close')">
        Fechar <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg>
      </button>
      <button v-if="replay" type="button" class="secondary compact-button" @click="$emit('replay')">Reiniciar</button>
    </header>

    <div v-if="switchToActive" class="active-context">
      <div>
        <p><span class="turn-state-dot" aria-hidden="true"></span> Turno {{ activeTurnNumber }} em andamento</p>
        <p v-if="activeConnection === 'Reconectando…' || activeConnection === 'Interrompida'" class="quiet-text" role="status">
          {{ activeConnection === 'Reconectando…' ? "Reconectando ao turno ativo…" : "Conexão com o turno ativo interrompida." }}
        </p>
      </div>
      <div class="active-context-actions">
        <button type="button" class="secondary compact-button" @click="$emit('active-turn')">Ir ao turno {{ activeTurnNumber }}</button>
        <button v-if="activeConnection === 'Interrompida'" type="button" class="secondary compact-button" @click="$emit('retry')">Tentar agora</button>
      </div>
    </div>

    <div v-else-if="active && !replay && activeTurnNumber" class="active-context">
      <p><span class="turn-state-dot" aria-hidden="true"></span> Ao vivo · {{ following ? "seguindo" : "inspecionando" }} o turno {{ activeTurnNumber }}</p>
      <button type="button" class="secondary compact-button" @click="followLatest">↓ Ir ao último evento</button>
    </div>

    <div ref="viewport" class="trajectory-scroll" @scroll.passive="onScroll">
      <div class="trajectory-context">
        <p v-if="replay" class="quiet-text">Replay de eventos gravados e sanitizados. Sem chamada de modelo.</p>
        <p v-if="prompt" class="trajectory-prompt">{{ prompt }}</p>
        <div v-if="connection === 'Reconectando…' || connection === 'Interrompida'" class="connection-notice">
          <p role="status">
            {{ connection === 'Reconectando…' ? 'Conexão perdida. Tentando reconectar.' : 'Fluxo interrompido. Os eventos exibidos permanecem disponíveis.' }}
            <span v-if="lastUpdateSeconds != null && connection === 'Reconectando…'"> Última atualização há {{ lastUpdateSeconds }} s.</span>
          </p>
          <button v-if="runId" type="button" class="secondary" @click="$emit('retry')">Tentar agora</button>
        </div>
        <p v-else-if="recovered != null" class="quiet-text" role="status">Reconectado · {{ recovered }} {{ recovered === 1 ? "evento recebido" : "eventos recebidos" }}</p>
        <dl class="trajectory-metrics">
          <div v-if="status"><dt>{{ active && connection ? connection : replay ? "Estado do replay" : "Estado do turno" }}</dt><dd>{{ status }}</dd></div>
          <div v-if="duration"><dt>{{ partial ? "Duração · trecho observado" : "Duração · medida pelo app" }}</dt><dd>{{ duration }}</dd></div>
          <div v-if="tokens"><dt>{{ tokens.label }}</dt><dd>{{ tokens.value }}</dd><dd v-if="tokenDetails.length > 1" class="token-details-cell"><details v-if="tokenDetails.length > 1" class="token-breakdown">
          <summary>Detalhar tokens</summary>
          <dl><div v-for="token in tokenDetails" :key="token.field"><dt>{{ token.label }}</dt><dd>{{ token.value }}</dd></div></dl>
        </details></dd></div>
          <div v-if="hasObservedActions">
            <dt>{{ partial ? "Trecho carregado" : active ? "Observadas até agora" : "Observadas" }}</dt>
            <dd>{{ actions }} {{ actions === 1 ? "ação" : "ações" }}<template v-if="failures != null && (failures || (!active && !partial))"> · {{ failures }} {{ failures === 1 ? "falha" : "falhas" }}</template></dd>
          </div>
        </dl>
        <p v-if="detail" class="trajectory-state-note">{{ detail }}</p>
        <div v-if="summary?.files.length || summary?.tests.length || files.length" class="trajectory-highlights">
        <section v-if="summary?.files.length" class="file-highlights" aria-label="Arquivos alterados observados">
          <h3>Arquivos alterados · {{ summary.files.length }}</h3>
          <ul>
            <li v-for="file in summary.files" :key="file.path">
              <button type="button" class="link-button" @click="showEvidence(file.item_id)"><code>{{ maskPaths(file.path, data.workspace) }}</code></button>
            </li>
          </ul>
        </section>
        <section v-else-if="!summary && files.length" class="file-highlights" aria-label="Arquivos alterados observados">
          <h3>Arquivos alterados <span v-if="nextBefore != null">· trecho carregado</span></h3>
          <ul>
            <li v-for="file in files" :key="file.path">
              <button type="button" class="link-button" @click="jumpTo(file.entry)"><code>{{ file.path }}</code></button>
            </li>
          </ul>
        </section>
        <section v-if="summary?.tests.length" class="file-highlights" aria-label="Comandos de teste observados">
          <h3>Testes</h3>
          <ul>
            <li v-for="(test, index) in summary.tests" :key="test.item_id">
              <button type="button" class="link-button" @click="showEvidence(test.item_id)">
                {{ index + 1 }}ª execução · {{ test.exit_code === 0 ? "Concluiu" : test.exit_code == null ? "Resultado indisponível" : `Falhou (saída ${test.exit_code})` }}
              </button>
            </li>
          </ul>
        </section>
        </div>
      </div>

      <p v-if="loading" class="pane-notice" role="status">Carregando trajetória…</p>
      <div v-else-if="error" class="pane-notice" role="alert">
        <p>{{ error }}</p>
        <button type="button" class="secondary" @click="$emit('retry-history')">Tentar novamente</button>
      </div>
      <section v-if="data.rows.length || nextBefore != null" class="trajectory-events" aria-labelledby="actions-title">
        <div class="pane-actions-heading">
          <h3 id="actions-title">Ações</h3>
          <div class="event-filters" aria-label="Filtrar eventos" role="group">
            <button v-for="[value, label] in [['essential', 'Essencial'], ['all', 'Tudo'], ['failures', 'Falhas']]" :key="value"
              type="button" :aria-pressed="filter === value" @click="filter = value; following = false">{{ label }}</button>
          </div>
        </div>
        <details v-if="filter === 'all' && index.length >= 8" class="action-index">
          <summary>Índice de ações <span>{{ index.length }}{{ nextBefore != null ? " carregadas" : "" }}</span></summary>
          <nav aria-label="Índice de ações da trajetória">
            <ol>
              <li v-for="entry in index" :key="entry.key">
                <button class="index-action" type="button" @click="jumpTo(entry)">{{ entry.title || entry.commandText || entry.tool?.name }}</button>
              </li>
            </ol>
          </nav>
        </details>
        <div v-if="nextBefore != null" class="history-controls">
          <button type="button" class="link-button" :aria-disabled="loadingEarlier" @click="earlier">
            {{ loadingEarlier ? "Carregando eventos anteriores…" : "Carregar eventos anteriores" }}
          </button>
          <p v-if="historyError" role="alert">{{ historyError }}</p>
        </div>
        <p v-if="linkedCall && !expanded && !loading && !loadingEarlier" class="pane-notice">
          {{ nextBefore != null ? "Carregue os eventos anteriores para localizar a ação deste link." : "A ação deste link não está nos eventos disponíveis." }}
        </p>
        <p v-if="!visible.length" class="empty-timeline">{{ filter === "failures" ? "Nenhuma falha nos eventos carregados." : "Nenhuma ação nos eventos carregados." }}</p>
        <ol id="timeline" ref="timeline" class="event-list">
          <li v-for="entry in visible" :key="entry.key"
            :data-entry-key="entry.key" :data-command-id="entry.command?.id" :data-command-status="(entry.command ?? entry.tool)?.status"
            :data-failure="entry.failed || undefined" :data-event-type="entry.event?.type"
            :class="{ 'trajectory-action': entry.command || entry.tool, 'trajectory-message': entry.markdown || entry.omitted || entry.event?.item?.type === 'reasoning' }">
            <TechnicalEvents v-if="entry.technicalEvents" :events="entry.technicalEvents" :title="entry.groupTitle">
              <template #entry="{ entry: item }">
                <TrajectoryEntry :entry="item" :selected="expanded === item.key" :terminal="data.terminal" :workspace="data.workspace"
                  :run-id="runId" @select="select" @announce="$emit('announce', $event)" />
              </template>
            </TechnicalEvents>
            <TrajectoryEntry v-else :entry="entry" :selected="expanded === entry.key" :terminal="data.terminal" :workspace="data.workspace"
              :run-id="runId" @select="select" @announce="$emit('announce', $event)" />
          </li>
        </ol>
      </section>
      <p v-else-if="!loading && !error && !data.rows.length" class="pane-notice" role="status">
        {{ active ? "Aguardando os primeiros eventos." : "Nenhum evento disponível para este turno." }}
      </p>
    </div>
    <div v-if="replay && !following && data.rows.length" class="trajectory-footer">
      <button type="button" class="secondary compact-button" @click="followLatest">Ir ao último evento <span aria-hidden="true">↓</span></button>
    </div>
  </aside>
</template>
