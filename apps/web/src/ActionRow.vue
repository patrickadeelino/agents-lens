<script setup>
import { computed } from "vue";
import { firstLine, lastNonEmptyLine } from "./eventPresentation.js";

const props = defineProps({
  entry: { type: Object, required: true },
  desktop: Boolean,
  selected: Boolean,
  terminal: Boolean,
});
defineEmits(["select"]);
const name = computed(() => props.entry.command
  ? props.entry.commandText || "Comando" : props.entry.tool.name);
const label = computed(() => props.entry.label === "Não confirmado" && !props.terminal
  ? "Resultado indisponível" : props.entry.label);
const accessibleName = computed(() => [
  name.value, label.value.toLowerCase(),
  props.entry.startedOffset ? `início ${props.entry.startedOffset.slice(1)}`
    : props.entry.offset ? `observado em ${props.entry.offset.slice(1)}` : null,
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
      <span v-if="entry.title || entry.tool" class="command-title">{{ entry.title || name }}</span>
      <code v-if="entry.command" class="command-literal" :title="name">{{ name }}</code>
    </span>
    <span class="command-status">{{ label }}</span>
    <span v-if="entry.duration" class="duration" title="Duração observada">{{ entry.duration }}</span>
    <span v-if="entry.preview" class="failure-preview">{{ entry.preview }}</span>
    <span v-else-if="entry.tool && (entry.failed || entry.toolPreview)" class="failure-preview">
      {{ entry.failed ? lastNonEmptyLine(entry.toolError) || entry.label : firstLine(entry.toolPreview) }}
    </span>
  </button>
</template>
