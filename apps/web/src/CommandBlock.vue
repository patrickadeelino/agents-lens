<script setup>
import { computed, onUnmounted, ref, useId } from "vue";
import { displayCommand, maskPaths } from "./eventPresentation.js";
import { formatShellCommand, shellCommandParts } from "./shellCommand.js";
import { t } from "./messages.js";

const props = defineProps({
  raw: { type: String, required: true },
  workspace: String,
  previewOnly: Boolean,
});
const emit = defineEmits(["announce"]);
const commandId = useId();
const showOriginal = ref(false);
const copied = ref(false);
const displayText = computed(() => maskPaths(showOriginal.value
  ? props.raw : formatShellCommand(displayCommand(props.raw)), props.workspace));
const parts = computed(() => shellCommandParts(displayText.value));
let copyTimer;
let disposed = false;

async function copyCommand() {
  try {
    await navigator.clipboard.writeText(props.raw);
  } catch {
    if (!disposed) emit("announce", t.action.commandCopyFailed);
    return;
  }
  if (disposed) return;
  copied.value = true;
  clearTimeout(copyTimer);
  copyTimer = setTimeout(() => { copied.value = false; }, 2000);
  emit("announce", t.action.commandCopied);
}

defineExpose({ copyCommand });

onUnmounted(() => {
  disposed = true;
  clearTimeout(copyTimer);
});
</script>

<template>
  <div class="command-block">
    <div class="command-surface" :class="{ 'command-surface-preview': previewOnly }">
      <pre :id="commandId" class="inspector-code command-code" tabindex="0" :aria-label="t.action.command"><code><span class="shell-prompt" aria-hidden="true">$ </span><template v-for="(part, index) in parts" :key="index"><span v-if="part.token" class="shell-token" :class="part.kind">{{ part.text }}</span><template v-else>{{ part.text }}</template></template></code></pre>
      <button
        v-if="!previewOnly" type="button" class="command-copy" :aria-label="t.action.copyCommand"
        :title="copied ? t.action.copied : t.action.copyCommand" @click="copyCommand"
      >
        <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path v-if="copied" d="m4 10 4 4 8-8" />
          <template v-else>
            <rect x="7" y="7" width="10" height="10" rx="1.5" />
            <path d="M13 7V4.5A1.5 1.5 0 0 0 11.5 3h-7A1.5 1.5 0 0 0 3 4.5v7A1.5 1.5 0 0 0 4.5 13H7" />
          </template>
        </svg>
      </button>
    </div>
    <button v-if="!previewOnly" type="button" class="link-button command-original" :aria-controls="commandId" :aria-expanded="showOriginal" @click="showOriginal = !showOriginal">
      <span aria-hidden="true">{{ showOriginal ? "▾" : "▸" }}</span>
      {{ showOriginal ? t.action.viewFormatted : t.action.viewOriginal }}
    </button>
  </div>
</template>
