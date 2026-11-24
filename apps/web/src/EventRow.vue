<script setup>
import { computed, ref, useId, watchEffect } from "vue";
import { renderAgentMarkdown } from "./markdown.js";

const props = defineProps({
  entry: { type: Object, required: true },
  selected: Boolean,
  final: Boolean,
  controls: String,
});
defineEmits(["select", "jump"]);
const message = ref(null);
const messageId = useId();
const expanded = ref(false);
const longMessage = ref(false);
const reasoning = computed(() => props.entry.event?.item?.type === "reasoning");
const html = computed(() => (
  !props.final && props.entry.markdown ? renderAgentMarkdown(props.entry.markdown) : ""
));

watchEffect((onCleanup) => {
  if (!message.value || !html.value) return;
  const element = message.value;
  const observer = new ResizeObserver(() => {
    longMessage.value = element.scrollHeight > 6 * parseFloat(getComputedStyle(element).lineHeight) + 1;
  });
  observer.observe(element);
  onCleanup(() => observer.disconnect());
}, { flush: "post" });
</script>

<template>
  <article v-if="entry.omitted || final || html || reasoning" class="event-card event-narrative">
    <span v-if="entry.offset" class="offset" title="Tempo decorrido na captura">{{ entry.offset }}</span>
    <span class="message-marker" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round">
        <path v-if="reasoning" d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8m-8 4h8"/>
        <path v-else d="M4 5h16v11h-9l-5 4v-4H4z"/>
      </svg>
    </span>
    <div class="narrative-content">
      <p v-if="entry.omitted" class="omitted-message">Mensagem intermediária omitida · conteúdo sanitizado</p>
      <template v-else-if="reasoning">
        <p class="run-kicker">Resumo de raciocínio</p>
        <p v-if="entry.text" class="event-text">{{ entry.text }}</p>
      </template>
      <p v-else-if="final" class="final-reply-row">
        <span class="run-kicker">Resposta final</span> · Ver no topo ↑
      </p>
      <template v-else>
        <p class="run-kicker">Codex</p>
        <div
          :id="messageId" ref="message" class="agent-message" :class="{ 'is-expanded': expanded, 'has-more': longMessage }"
          :inert="longMessage && !expanded" v-html="html"
        ></div>
        <button
          v-if="longMessage" class="link-button message-toggle" type="button"
          :aria-expanded="expanded" :aria-controls="messageId" @click="expanded = !expanded"
        >{{ expanded ? "Mostrar menos" : "Mostrar mais" }}</button>
      </template>
    </div>
    <button
      v-if="!entry.omitted" type="button" class="event-select"
      :aria-label="[final ? 'Resposta final · Ver no topo' : reasoning ? 'Inspecionar resumo de raciocínio' : 'Inspecionar mensagem do Codex', entry.offset].filter(Boolean).join(' ')"
      :aria-current="!final && selected ? 'true' : undefined"
      :aria-controls="controls" :aria-expanded="controls ? selected : undefined"
      @click="final ? $emit('jump') : $emit('select', entry, $event.currentTarget)"
    ><span class="visually-hidden">Inspecionar mensagem do Codex</span></button>
  </article>
  <article v-else class="event-card">
    <div class="event-heading">
      <span class="event-label">{{ entry.label }}</span>
      <span class="offset" title="Tempo decorrido na captura">{{ entry.offset }}</span>
    </div>
    <p v-if="entry.text" class="event-text">{{ entry.text }}</p>
    <ul v-if="entry.tasks.length" class="event-tasks">
      <li v-for="(task, index) in entry.tasks" :key="index">
        <span aria-hidden="true">{{ task.completed ? "✓" : "◌" }}</span>
        <span class="visually-hidden">{{ task.completed ? "Concluída" : "Pendente" }}</span>
        {{ task.text }}
      </li>
    </ul>
    <button
      type="button"
      class="event-select"
      :aria-label="['Inspecionar', entry.label, entry.offset].filter(Boolean).join(' ')"
      :aria-current="selected ? 'true' : undefined"
      :aria-controls="controls" :aria-expanded="controls ? selected : undefined"
      @click="$emit('select', entry, $event.currentTarget)"
    ><span class="visually-hidden">Inspecionar evento</span></button>
  </article>
</template>
