<script setup>
import { computed } from "vue";
import { renderAgentMarkdown } from "./markdown.js";

const props = defineProps({
  entry: { type: Object, required: true },
  selected: Boolean,
});
defineEmits(["select"]);
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
    <div v-if="html" class="agent-message" v-html="html"></div>
    <p v-else-if="entry.text" class="event-text">{{ entry.text }}</p>
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
