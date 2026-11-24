<script setup>
import { computed, ref, useId } from "vue";
import { maskPaths } from "./eventPresentation.js";
import { renderAgentMarkdown } from "./markdown.js";

const props = defineProps({
  turn: { type: Object, required: true },
  number: { type: Number, required: true },
  selected: Boolean,
  live: Boolean,
  activity: String,
  workspace: String,
  observed: Object,
});
defineEmits(["inspect"]);
const clock = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });
const timestamp = computed(() => props.turn.submitted_at ?? props.turn.finished_at);
const time = computed(() => {
  const date = new Date(timestamp.value);
  return timestamp.value && Number.isFinite(date.getTime()) ? clock.format(date) : null;
});
const reply = computed(() => maskPaths(props.turn.assistant_markdown, props.workspace));
const html = computed(() => expanded.value && reply.value ? renderAgentMarkdown(reply.value) : "");
const preview = computed(() => reply.value?.replace(/!?\[([^\]]+)\]\([^)]*\)/g, "$1")
  .replace(/(^|\n)[#>]+\s*/g, "$1").replace(/[*`]/g, "").replace(/\s+/g, " ").trim());
const replyId = useId();
const expanded = ref(false);
const status = computed(() => ({
  active: props.live ? "Em andamento · ao vivo" : "Em andamento", completed: "Concluído", failed: "Falhou", unconfirmed: "Resultado não confirmado",
})[props.turn.status] ?? "Estado indisponível");

</script>

<template>
  <li class="turn-card" :class="{ 'turn-selected': selected }" :data-turn-run="turn.run_id" :data-turn-status="status">
    <article :aria-label="`Turno ${number}`">
      <header class="turn-card-meta">
        <h2>Turno {{ number }}</h2>
        <time v-if="time" :datetime="timestamp">{{ time }}</time>
      </header>
      <p class="turn-prompt">{{ turn.prompt ?? "Mensagem original indisponível" }}</p>
      <p v-if="!reply" class="turn-reply-preview">{{ activity || (turn.status === 'active' ? 'Aguardando resposta…' : 'Resposta indisponível.') }}</p>
      <button v-if="reply" type="button" class="link-button reply-toggle" :class="{ 'turn-reply-preview': !expanded }" :aria-label="expanded ? undefined : `Ler resposta completa do turno ${number}`" :aria-controls="replyId"
        :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? "Recolher resposta" : preview || "Ler resposta completa" }}</button>
      <div v-if="reply" :id="replyId" v-show="expanded" class="agent-message turn-reply" v-html="html"></div>
      <footer class="turn-card-footer">
        <div class="turn-state-group">
          <span class="turn-state" :data-state="turn.status">
            <span class="turn-state-dot" aria-hidden="true">
              <svg v-if="turn.status === 'completed'" viewBox="0 0 24 24"><path d="m6 12 4 4 8-8"/></svg>
              <svg v-else-if="turn.status === 'failed'" viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17"/></svg>
            </span>
            {{ status }}
          </span>
          <span v-if="observed" class="turn-action-count">{{ observed.actions }} {{ observed.actions === 1 ? "ação" : "ações" }}{{ observed.partial ? " observadas" : "" }}</span>
          <span v-if="observed?.files?.length" class="turn-action-count">{{ observed.files.length }} {{ observed.files.length === 1 ? "arquivo alterado" : "arquivos alterados" }}</span>
          <span v-if="observed?.failures" class="turn-failure-count">{{ observed.failures }} {{ observed.failures === 1 ? "falha" : "falhas" }}</span>
        </div>
        <button type="button" class="link-button inspect-turn" :aria-controls="selected ? 'trajectory-pane' : undefined"
          :aria-expanded="selected" :aria-label="selected ? `Turno ${number} em inspeção` : `Ver trajetória do turno ${number}`"
          @click="$emit('inspect', turn.run_id)">
          <span v-if="selected" class="selected-dot" aria-hidden="true"></span>
          {{ selected ? "Em inspeção" : "Ver trajetória" }}
          <svg v-if="!selected" aria-hidden="true" viewBox="0 0 24 24"><path d="M4 12h15m-6-6 6 6-6 6"/></svg>
        </button>
      </footer>
    </article>
  </li>
</template>
