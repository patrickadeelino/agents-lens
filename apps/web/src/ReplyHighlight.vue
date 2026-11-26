<script setup>
import { computed } from "vue";
import { useAgentName } from "./agents.js";
import { renderAgentMarkdown } from "./markdown.js";
import { t } from "./messages.js";

const props = defineProps({
  entry: { type: Object, required: true },
});
defineEmits(["jump"]);

const agent = useAgentName();
const html = computed(() => renderAgentMarkdown(props.entry.markdown));
</script>

<template>
  <article class="reply-highlight" tabindex="-1" aria-labelledby="reply-title">
    <header>
      <h2 id="reply-title">{{ t.event.latestMessage(agent) }}</h2>
      <button type="button" class="link-button" @click="$emit('jump', entry.key)">{{ t.event.viewInTrajectory }}</button>
    </header>
    <div class="agent-message" v-html="html"></div>
  </article>
</template>
