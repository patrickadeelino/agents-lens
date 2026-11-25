<script setup>
import { computed } from "vue";
import { firstLine, lastNonEmptyLine } from "./eventPresentation.js";

const props = defineProps({
  entry: { type: Object, required: true },
  desktop: Boolean,
  selected: Boolean,
  terminal: Boolean,
  controls: String,
});
defineEmits(["select"]);
const name = computed(() => props.entry.command
  ? props.entry.commandText || "Comando" : props.entry.tool.name);
const title = computed(() => {
  if (props.entry.event?.item?.type === "file_change") return props.entry.toolPreview
    ? `Alteração em ${props.entry.toolPreview}` : "Alteração de arquivos";
  if (/^(?:(?:rtk(?: proxy)?|uv run)\s+)*(?:pytest|python(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest))(?:\s|$)/.test(name.value)) return "Executar testes";
  return props.entry.title || (props.entry.command ? "Executar comando" : name.value);
});
const fileChange = computed(() => props.entry.event?.item?.type === "file_change");
const label = computed(() => props.entry.statusKey === "result_unconfirmed" && !props.terminal
  ? "Resultado indisponível" : props.entry.label);
const conflicting = computed(() => Boolean((props.entry.command ?? props.entry.tool).conflictingResults));
const accessibleName = computed(() => [
  name.value, label.value.toLowerCase(),
  props.entry.startedOffset ? `início ${props.entry.startedOffset.slice(1)}`
    : props.entry.offset ? `observado em ${props.entry.offset.slice(1)}` : null,
  conflicting.value ? "resultados divergentes" : null,
].filter(Boolean).join(", "));
</script>

<template>
  <button
    class="command-summary command-select"
    type="button"
    :aria-controls="controls ?? (desktop && selected ? 'action-inspector' : undefined)"
    :aria-expanded="controls ? selected : undefined"
    :aria-current="selected ? 'true' : undefined"
    :aria-label="accessibleName"
    @click="$emit('select', entry, $event.currentTarget)"
  >
    <span class="status-symbol action-marker" aria-hidden="true"><span v-if="fileChange && !entry.failed" class="file-marker" aria-hidden="true">✎</span><template v-else>{{ entry.icon }}</template></span>
    <span class="command-content">
      <span class="command-title">{{ title }}</span>
      <code v-if="entry.command" class="command-literal" :title="name">{{ name }}</code>
    </span>
    <span class="command-status">{{ fileChange && !entry.failed && entry.statusKey === "completed" ? "Alteração" : label }}</span>
    <span v-if="entry.offset" class="command-times command-offset" title="Primeira observação na captura">{{ entry.offset }}</span>
    <span v-if="conflicting" class="failure-preview">Resultados divergentes</span>
    <span v-else-if="entry.preview && !selected" class="failure-preview">{{ entry.preview }}</span>
    <span v-else-if="entry.tool && !fileChange && (entry.failed || entry.toolPreview)" class="failure-preview">
      {{ entry.failed ? lastNonEmptyLine(entry.toolError) || entry.label : firstLine(entry.toolPreview) }}
    </span>
  </button>
</template>
