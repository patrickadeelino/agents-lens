<script setup>
import { displayCommand, firstLine } from "./eventPresentation.js";

defineProps({
  entry: { type: Object, required: true },
  desktop: Boolean,
  selected: Boolean,
});
defineEmits(["select"]);
</script>

<template>
  <button
    class="command-summary command-select"
    type="button"
    :aria-controls="desktop && selected ? 'action-inspector' : undefined"
    :aria-pressed="selected"
    @click="$emit('select', entry, $event.currentTarget)"
  >
    <span class="command-offset" title="Primeira observação na captura">{{ entry.offset }}</span>
    <span class="status-symbol action-marker" aria-hidden="true">{{ entry.icon }}</span>
    <span class="command-content">
      <code v-if="entry.command" class="command-literal">{{ displayCommand(entry.command.command) || "Comando" }}</code>
      <span v-else class="command-title">{{ entry.tool.name }}</span>
    </span>
    <span class="command-status">{{ entry.label }}</span>
    <span v-if="entry.duration" class="duration" title="Duração observada">{{ entry.duration }}</span>
    <span v-if="entry.preview" class="failure-preview">{{ entry.preview }}</span>
    <span v-else-if="entry.tool && (entry.failed || entry.tool.preview)" class="failure-preview">
      {{ entry.failed ? firstLine(entry.tool.error) || entry.label : firstLine(entry.tool.preview) }}
    </span>
  </button>
</template>
