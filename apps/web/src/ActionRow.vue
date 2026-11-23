<script setup>
import { computed } from "vue";
import { displayCommand, firstLine } from "./eventPresentation.js";

const props = defineProps({
  entry: { type: Object, required: true },
  desktop: Boolean,
  selected: Boolean,
});
defineEmits(["select"]);
const name = computed(() => props.entry.command
  ? displayCommand(props.entry.command.command) || "Comando" : props.entry.tool.name);
const accessibleName = computed(() => [
  name.value, props.entry.label,
  props.entry.offset ? `início ${props.entry.offset.slice(1)}` : null,
  props.entry.duration,
].filter(Boolean).join(", "));
</script>

<template>
  <button
    class="command-summary command-select"
    type="button"
    :aria-controls="desktop && selected ? 'action-inspector' : undefined"
    :aria-current="selected ? 'true' : undefined"
    :aria-label="accessibleName"
    @click="$emit('select', entry, $event.currentTarget)"
  >
    <span class="command-offset" title="Primeira observação na captura">{{ entry.offset }}</span>
    <span class="status-symbol action-marker" aria-hidden="true">{{ entry.icon }}</span>
    <span class="command-content">
      <code v-if="entry.command" class="command-literal" :title="name">{{ name }}</code>
      <span v-else class="command-title">{{ name }}</span>
    </span>
    <span class="command-status">{{ entry.label }}</span>
    <span v-if="entry.duration" class="duration" title="Duração observada">{{ entry.duration }}</span>
    <span v-if="entry.preview" class="failure-preview">{{ entry.preview }}</span>
    <span v-else-if="entry.tool && (entry.failed || entry.tool.preview)" class="failure-preview">
      {{ entry.failed ? firstLine(entry.tool.error) || entry.label : firstLine(entry.tool.preview) }}
    </span>
  </button>
</template>
