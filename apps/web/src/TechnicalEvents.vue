<script setup>
import { computed, ref } from "vue";
import EventRow from "./EventRow.vue";

const props = defineProps({
  events: { type: Array, required: true },
  selectedKey: String,
});
defineEmits(["select"]);
const expanded = ref(false);
const title = computed(() => {
  if (props.events.length === 1) return props.events[0].label;
  return props.events.some(({ event }) => event.type === "thread.started" || event.type === "turn.started")
    ? "Inicialização e diagnósticos"
    : "Eventos técnicos";
});
</script>

<template>
  <details class="technical-events" @toggle="expanded = $event.currentTarget.open">
    <summary>
      <span>{{ title }}</span>
      <span v-if="events.length > 1" class="technical-count">{{ ` · ${events.length} eventos` }}</span>
    </summary>
    <ol v-if="expanded" class="technical-event-list">
      <li v-for="entry in events" :id="`timeline-${entry.key}`" :key="entry.key">
        <EventRow :entry="entry" :selected="selectedKey === entry.key" @select="(...args) => $emit('select', ...args)" />
      </li>
    </ol>
  </details>
</template>
