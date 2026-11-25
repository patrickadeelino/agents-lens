<script setup>
import { computed } from "vue";
import { renderAgentMarkdown } from "./markdown.js";
import { t } from "./messages.js";

const props = defineProps({
  entry: { type: Object, required: true },
});
defineEmits(["jump"]);

const html = computed(() => renderAgentMarkdown(props.entry.markdown));
</script>

<template>
  <article class="reply-highlight" tabindex="-1" aria-labelledby="reply-title">
    <header>
      <h2 id="reply-title">{{ t.event.latestMessage }}</h2>
      <button type="button" class="link-button" @click="$emit('jump', entry.key)">{{ t.event.viewInTrajectory }}</button>
    </header>
    <div class="agent-message" v-html="html"></div>
  </article>
</template>
