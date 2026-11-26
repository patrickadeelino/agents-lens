<script setup>
import { computed, ref, useId, watchEffect } from "vue";
import { useAgentName } from "./agents.js";
import { renderAgentMarkdown } from "./markdown.js";
import { t } from "./messages.js";

const props = defineProps({
  entry: { type: Object, required: true },
  selected: Boolean,
  final: Boolean,
  controls: String,
});
defineEmits(["select"]);
const agent = useAgentName();
const revealed = ref(false);
const collapsed = computed(() => props.final && !revealed.value);
const message = ref(null);
const messageId = useId();
const expanded = ref(false);
const longMessage = ref(false);
const reasoning = computed(() => props.entry.event?.item?.type === "reasoning");
const html = computed(() => (
  !collapsed.value && props.entry.markdown ? renderAgentMarkdown(props.entry.markdown) : ""
));

watchEffect((onCleanup) => {
  if (!message.value || !html.value) return;
  const element = message.value;
  const observer = new ResizeObserver(() => {
    longMessage.value = element.scrollHeight > 6 * parseFloat(getComputedStyle(element).lineHeight) + 1;
  });
  observer.observe(element);
  onCleanup(() => observer.disconnect());
}, { flush: "post" });
</script>

<template>
  <article v-if="entry.omitted || final || html || reasoning" class="event-card event-narrative">
    <span v-if="entry.offset" class="offset" :title="t.event.elapsed">{{ entry.offset }}</span>
    <span class="message-marker" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round">
        <path v-if="reasoning" d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8m-8 4h8"/>
        <path v-else d="M4 5h16v11h-9l-5 4v-4H4z"/>
      </svg>
    </span>
    <div class="narrative-content">
      <p v-if="entry.omitted" class="omitted-message">{{ t.event.omitted }}</p>
      <template v-else-if="reasoning">
        <p class="run-kicker">{{ t.event.reasoning }}</p>
        <p v-if="entry.text" class="event-text">{{ entry.text }}</p>
      </template>
      <template v-else-if="final">
        <p class="final-reply-row">
          <span class="run-kicker">{{ t.event.finalReply }}</span>
          <span v-if="collapsed">{{ t.event.shownInConversation }}</span>
          <button type="button" class="link-button message-toggle" :aria-expanded="!collapsed" @click="revealed = !revealed">
            {{ collapsed ? t.event.showHere : t.event.showLess }}
          </button>
        </p>
        <div v-if="html" class="agent-message final-reply-text" v-html="html"></div>
      </template>
      <template v-else>
        <p class="run-kicker">{{ agent }}</p>
        <div
          :id="messageId" ref="message" class="agent-message" :class="{ 'is-expanded': expanded, 'has-more': longMessage }"
          :inert="longMessage && !expanded" v-html="html"
        ></div>
        <button
          v-if="longMessage" class="link-button message-toggle" type="button"
          :aria-expanded="expanded" :aria-controls="messageId" @click="expanded = !expanded"
        >{{ expanded ? t.event.showLess : t.event.showMore }}</button>
      </template>
    </div>
    <button
      v-if="!entry.omitted && !final" type="button" class="event-select"
      :aria-label="[reasoning ? t.event.inspectReasoning : t.event.inspectMessage(agent), entry.offset].filter(Boolean).join(' ')"
      :aria-current="selected ? 'true' : undefined"
      :aria-controls="controls" :aria-expanded="controls ? selected : undefined"
      @click="$emit('select', entry, $event.currentTarget)"
    ><span class="visually-hidden">{{ t.event.inspectMessage(agent) }}</span></button>
  </article>
  <article v-else class="event-card">
    <div class="event-heading">
      <span class="event-label">{{ entry.label }}</span>
      <span class="offset" :title="t.event.elapsed">{{ entry.offset }}</span>
    </div>
    <p v-if="entry.text" class="event-text">{{ entry.text }}</p>
    <ul v-if="entry.tasks.length" class="event-tasks">
      <li v-for="(task, index) in entry.tasks" :key="index">
        <span aria-hidden="true">{{ task.completed ? "✓" : "◌" }}</span>
        <span class="visually-hidden">{{ task.completed ? t.event.taskDone : t.event.taskPending }}</span>
        {{ task.text }}
      </li>
    </ul>
    <button
      type="button"
      class="event-select"
      :aria-label="[t.event.inspect, entry.label, entry.offset].filter(Boolean).join(' ')"
      :aria-current="selected ? 'true' : undefined"
      :aria-controls="controls" :aria-expanded="controls ? selected : undefined"
      @click="$emit('select', entry, $event.currentTarget)"
    ><span class="visually-hidden">{{ t.event.inspectEvent }}</span></button>
  </article>
</template>
