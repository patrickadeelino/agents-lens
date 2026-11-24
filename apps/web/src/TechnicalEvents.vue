<script setup>
import { computed } from "vue";

const props = defineProps({
  events: { type: Array, required: true },
  title: String,
});
const title = computed(() => {
  if (props.title) return props.title;
  if (props.events.length === 1) return props.events[0].label;
  return props.events.some(({ event }) => event.type === "thread.started" || event.type === "turn.started")
    ? "Inicialização e diagnósticos"
    : "Eventos técnicos";
});
</script>

<template>
  <details class="technical-events">
    <summary>
      <span>{{ title }}</span>
      <span v-if="events.length > 1" class="technical-count">{{ ` · ${events.length} eventos` }}</span>
    </summary>
    <ol class="technical-event-list">
      <li
        v-for="entry in events" :id="`timeline-${entry.key}`" :key="entry.key"
        :data-entry-key="entry.key" :data-command-id="entry.command?.id" :data-command-status="entry.command?.status"
        :data-failure="entry.failed || undefined"
      >
        <slot name="entry" :entry="entry" />
      </li>
    </ol>
  </details>
</template>
