<script setup>
import { computed, inject } from "vue";
import { firstLine, lastNonEmptyLine, relativePath } from "./eventPresentation.js";
import { t } from "./messages.js";

const props = defineProps({
  entry: { type: Object, required: true },
  desktop: Boolean,
  selected: Boolean,
  terminal: Boolean,
  controls: String,
});
defineEmits(["select"]);
const folder = inject("folder", null);
const name = computed(() => props.entry.command
  ? props.entry.commandText || t.action.command : props.entry.tool.name);
const title = computed(() => {
  if (props.entry.event?.item?.type === "file_change") return props.entry.toolPreview
    ? t.action.fileChanged(relativePath(props.entry.toolPreview, folder?.value)) : t.tools.fileChanges;
  if (/^(?:(?:rtk(?: proxy)?|uv run)\s+)*(?:pytest|python(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest))(?:\s|$)/.test(name.value)) return t.commandTitles.tests;
  return props.entry.title || (props.entry.command ? t.commandTitles.run : name.value);
});
const fileChange = computed(() => props.entry.event?.item?.type === "file_change");
const label = computed(() => props.entry.statusKey === "result_unconfirmed" && !props.terminal
  ? t.status.resultUnavailable : props.entry.label);
const conflicting = computed(() => Boolean((props.entry.command ?? props.entry.tool).conflictingResults));
const accessibleName = computed(() => [
  name.value, label.value.toLowerCase(),
  props.entry.startedOffset ? t.action.startedAt(props.entry.startedOffset.slice(1))
    : props.entry.offset ? t.action.observedAt(props.entry.offset.slice(1)) : null,
  conflicting.value ? t.action.conflicting : null,
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
    <span class="command-status" :data-tone="entry.failed || conflicting ? 'attention' : entry.statusKey === 'completed' ? 'quiet' : 'open'">{{ fileChange && !entry.failed && entry.statusKey === "completed" ? t.action.fileChange : label }}</span>
    <span v-if="entry.offset" class="command-times command-offset" :title="t.action.firstObservation">{{ entry.offset }}</span>
    <span v-if="conflicting" class="failure-preview">{{ t.action.conflictingTitle }}</span>
    <span v-else-if="entry.preview && !selected" class="failure-preview">{{ entry.preview }}</span>
    <span v-else-if="entry.tool && !fileChange && (entry.failed || entry.toolPreview)" class="failure-preview">
      {{ entry.failed ? lastNonEmptyLine(entry.toolError) || entry.label : firstLine(entry.toolPreview) }}
    </span>
  </button>
</template>
