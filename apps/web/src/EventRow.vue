<script setup>
import { computed, ref, useId, watchEffect } from "vue";
import { renderAgentMarkdown } from "./markdown.js";

const props = defineProps({
  entry: { type: Object, required: true },
  selected: Boolean,
  final: Boolean,
});
defineEmits(["select", "jump"]);
const message = ref(null);
const messageId = useId();
const expanded = ref(false);
const longMessage = ref(false);
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
  <article v-if="entry.omitted" class="event-card event-narrative omitted-message">
    <span class="offset" title="Tempo decorrido na captura">{{ entry.offset }}</span>
    <span class="message-marker" aria-hidden="true"></span>
    <p>Mensagem intermediária omitida · conteúdo sanitizado</p>
  </article>
  <article v-else-if="final || html" class="event-card event-narrative">
    <span class="offset" title="Tempo decorrido na captura">{{ entry.offset }}</span>
    <span class="message-marker" aria-hidden="true"></span>
    <div class="narrative-content">
      <p v-if="final" class="final-reply-row">
        <span class="run-kicker">Resposta final</span> · Ver no topo ↑
      </p>
      <template v-else>
        <p class="run-kicker">Codex</p>
        <div
          :id="messageId" ref="message" class="agent-message" :class="{ 'is-expanded': expanded }"
          :inert="longMessage && !expanded" v-html="html"
        ></div>
        <button
          v-if="longMessage" class="link-button message-toggle" type="button"
          :aria-expanded="expanded" :aria-controls="messageId" @click="expanded = !expanded"
        >{{ expanded ? "Mostrar menos" : "Mostrar mais" }}</button>
      </template>
    </div>
    <button
      type="button" class="event-select"
      :aria-label="[final ? 'Resposta final · Ver no topo' : 'Inspecionar mensagem do Codex', entry.offset].filter(Boolean).join(' ')"
      :aria-current="!final && selected ? 'true' : undefined"
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
      @click="$emit('select', entry, $event.currentTarget)"
    ><span class="visually-hidden">Inspecionar evento</span></button>
  </article>
</template>
