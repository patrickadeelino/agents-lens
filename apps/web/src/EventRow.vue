<script setup>
import { computed, ref } from "vue";
import { renderAgentMarkdown } from "./markdown.js";

const props = defineProps({
  entry: { type: Object, required: true },
  condensed: Boolean,
});
const jsonOpen = ref(false);
const html = computed(() => (
  props.entry.markdown ? renderAgentMarkdown(props.entry.markdown) : ""
));
</script>

<template>
  <article class="event-card">
    <div class="event-heading">
      <span class="event-label">{{ entry.label }}</span>
      <span class="offset" title="Tempo decorrido na captura">{{ entry.offset }}</span>
    </div>
    <details v-if="html && condensed" class="inline-reply">
      <summary>Ver mensagem</summary>
      <div class="agent-message" v-html="html"></div>
    </details>
    <div v-else-if="html" class="agent-message" v-html="html"></div>
    <p v-else-if="entry.text" class="event-text">{{ entry.text }}</p>
    <ul v-if="entry.tasks.length" class="event-tasks">
      <li v-for="(task, index) in entry.tasks" :key="index">
        <span aria-hidden="true">{{ task.completed ? "✓" : "◌" }}</span>
        <span class="visually-hidden">{{ task.completed ? "Concluída" : "Pendente" }}</span>
        {{ task.text }}
      </li>
    </ul>
    <details class="raw-event" @toggle="jsonOpen = $event.currentTarget.open">
      <summary>Ver evento JSON</summary>
      <pre v-if="jsonOpen" tabindex="0">{{ JSON.stringify(entry.event, null, 2) }}</pre>
    </details>
  </article>
</template>
