<script setup>
import { computed } from "vue";
import { renderAgentMarkdown } from "./markdown.js";

const props = defineProps({
  entry: { type: Object, required: true },
});
defineEmits(["jump"]);

const shortMessage = computed(() => props.entry.markdown.length <= 600
  && props.entry.markdown.split("\n", 9).length <= 8);
const html = computed(() => renderAgentMarkdown(props.entry.markdown));
</script>

<template>
  <article class="reply-highlight" aria-labelledby="reply-title">
    <header>
      <h2 id="reply-title">Última mensagem do Codex</h2>
      <button type="button" class="link-button" @click="$emit('jump', entry.key)">Ver na trajetória</button>
    </header>
    <div v-if="shortMessage" class="agent-message" v-html="html"></div>
    <details v-else>
      <summary>Ler mensagem</summary>
      <div class="agent-message" v-html="html"></div>
    </details>
  </article>
</template>
