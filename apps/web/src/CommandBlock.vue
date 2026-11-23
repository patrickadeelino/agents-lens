<script setup>
import { computed, onUnmounted, ref, useId } from "vue";
import { displayCommand, maskPaths } from "./eventPresentation.js";
import { formatShellCommand, shellCommandParts } from "./shellCommand.js";

const props = defineProps({
  raw: { type: String, required: true },
  workspace: String,
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
    if (!disposed) emit("announce", "Não foi possível copiar o comando.");
    return;
  }
  if (disposed) return;
  copied.value = true;
  clearTimeout(copyTimer);
  copyTimer = setTimeout(() => { copied.value = false; }, 2000);
  emit("announce", "Comando copiado");
}

onUnmounted(() => {
  disposed = true;
  clearTimeout(copyTimer);
});
</script>

<template>
  <div class="command-block">
    <div class="command-surface">
      <button type="button" class="link-button command-copy" aria-label="Copiar comando" @click="copyCommand">
        {{ copied ? "Copiado" : "Copiar" }}
      </button>
      <pre :id="commandId" class="inspector-code command-code" tabindex="0" aria-label="Comando"><code><span class="shell-prompt" aria-hidden="true">$ </span><template v-for="(part, index) in parts" :key="index"><span v-if="part.token" class="shell-token" :class="part.kind">{{ part.text }}</span><template v-else>{{ part.text }}</template></template></code></pre>
    </div>
    <button type="button" class="link-button command-original" :aria-controls="commandId" :aria-expanded="showOriginal" @click="showOriginal = !showOriginal">
      <span aria-hidden="true">{{ showOriginal ? "▾" : "▸" }}</span>
      {{ showOriginal ? "Ver formatado" : "Ver original" }}
    </button>
  </div>
</template>
