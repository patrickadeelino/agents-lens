<script setup>
import { computed, ref, useId, watchEffect } from "vue";

const props = defineProps({
  entry: { type: Object, required: true },
  label: String,
  mobile: Boolean,
});
const title = computed(() => props.entry.title || props.entry.commandText || props.entry.tool?.name || props.entry.label);
const kind = computed(() => {
  if (props.entry.command) return "Comando";
  if (props.entry.markdown) return "Mensagem";
  return props.entry.tool ? "Ferramenta" : "Evento";
});
const text = ref(null);
const textId = useId();
const expanded = ref(false);
const longTitle = ref(false);

watchEffect((onCleanup) => {
  if (!text.value) return;
  const element = text.value;
  const observer = new ResizeObserver(() => {
    const titleLine = element.firstElementChild;
    longTitle.value = element.scrollHeight > 3 * parseFloat(getComputedStyle(titleLine).lineHeight) + 1;
  });
  observer.observe(element);
  onCleanup(() => observer.disconnect());
}, { flush: "post" });
</script>

<template>
  <header class="inspector-heading">
    <span class="run-kicker">{{ kind }}</span>
    <p v-if="entry.command || entry.tool || entry.failed" class="command-status">
      <span class="status-symbol" aria-hidden="true">{{ entry.icon || "×" }}</span>
      {{ entry.icon ? label : "Falhou" }}
    </p>
    <div :id="textId" ref="text" class="inspector-title" :class="{ 'is-expanded': expanded }">
      <component :is="mobile ? 'h1' : 'h2'" :id="mobile ? 'command-detail-title' : undefined" :class="{ 'inspector-command': entry.command && !entry.title }">
        {{ title }}
      </component>
      <code v-if="entry.command && entry.title" class="inspector-command inspector-literal">{{ entry.commandText }}</code>
    </div>
    <button v-if="longTitle" class="link-button inspector-title-toggle" type="button" :aria-expanded="expanded" :aria-controls="textId" @click="expanded = !expanded">
      {{ expanded ? "Recolher título" : "Mostrar tudo" }}
    </button>
  </header>
</template>
