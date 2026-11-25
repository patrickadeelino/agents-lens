<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { groupConversations } from "./conversationNavigation.js";
import { t } from "./messages.js";

const props = defineProps({
  threads: { type: Array, required: true },
  currentRoot: String,
  loading: Boolean,
  error: Boolean,
  drawer: Boolean,
});
defineEmits(["new", "open", "retry", "close"]);
const groups = computed(() => groupConversations(props.threads));
const closeButton = ref(null);
const panel = ref(null);
const date = new Intl.DateTimeFormat("en-US", { day: "2-digit", month: "2-digit" });
const labels = {
  active: t.status.inProgress, completed: t.status.completed, failed: t.status.failed, unconfirmed: t.status.actionUnconfirmed,
};

function threadLink(thread) {
  const params = thread.thread_id ? { thread_id: thread.thread_id } : { run_id: thread.latest_run_id };
  return `?${new URLSearchParams(params)}`;
}

function activityDate(value) {
  const time = new Date(value);
  return value && Number.isFinite(time.getTime()) ? date.format(time) : null;
}

function keepFocus(event) {
  if (!props.drawer) return;
  const controls = [...panel.value.querySelectorAll("button:not([disabled]), a[href], summary")].filter((item) => item.offsetParent);
  const start = controls[0];
  const end = controls.at(-1);
  if (event.shiftKey && document.activeElement === start) {
    event.preventDefault();
    end?.focus();
  } else if (!event.shiftKey && document.activeElement === end) {
    event.preventDefault();
    start?.focus();
  }
}

watch(() => props.drawer, async (open) => {
  if (!open) return;
  await nextTick();
  closeButton.value?.focus({ preventScroll: true });
});
</script>

<template>
  <button v-if="drawer" type="button" class="navigation-scrim" tabindex="-1" aria-hidden="true" @click="$emit('close')"></button>
  <aside id="conversation-sidebar" ref="panel" class="conversation-sidebar" :class="{ 'drawer-open': drawer }"
    :role="drawer ? 'dialog' : undefined" :aria-modal="drawer ? 'true' : undefined"
    :aria-label="drawer ? t.sidebar.history : undefined" @keydown.tab="keepFocus">
    <header class="sidebar-header">
      <span class="wordmark">{{ t.common.appName }}</span>
      <button ref="closeButton" class="icon-button drawer-close" type="button" :aria-label="t.sidebar.closeHistory" @click="$emit('close')">
        <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg>
      </button>
    </header>
    <button class="new-conversation" type="button" @click="$emit('new')">
      <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
      {{ t.common.newConversation }}
    </button>

    <nav class="sidebar-history" aria-labelledby="conversation-list-title">
      <h2 id="conversation-list-title">{{ t.sidebar.folders }}</h2>
      <p v-if="loading && !threads.length" class="quiet-text" role="status">{{ t.sidebar.loading }}</p>
      <div v-else-if="error" class="sidebar-notice" role="alert">
        <p>{{ t.sidebar.loadFailed }}</p>
        <button type="button" class="link-button" @click="$emit('retry')">{{ t.common.tryAgain }}</button>
      </div>
      <p v-else-if="!threads.length" class="quiet-text">{{ t.sidebar.empty }}</p>
      <section v-for="group in groups" :key="group.path ?? ''" class="workspace-group">
        <details open>
          <summary :title="group.path ?? undefined">
            <span>{{ group.name }}</span>
          </summary>
          <ul>
            <li v-for="thread in group.threads" :key="thread.root_run_id">
              <a class="history-link" :href="threadLink(thread)" :aria-current="currentRoot === thread.root_run_id ? 'page' : undefined"
                @click="$emit('open', $event)">
                <span class="turn-state-dot history-state" :data-state="thread.status" aria-hidden="true">
                  <svg v-if="thread.status === 'completed'" viewBox="0 0 24 24"><path d="m6 12 4 4 8-8"/></svg>
                  <svg v-else-if="thread.status === 'failed'" viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17"/></svg>
                </span>
                <strong>{{ thread.title }}</strong>
                <small>{{ thread.turns && thread.status === "active" ? t.sidebar.turnProgress(thread.turns.length) : labels[thread.status] }}<template v-if="activityDate(thread.last_activity_at)"> · {{ activityDate(thread.last_activity_at) }}</template></small>
              </a>
            </li>
          </ul>
        </details>
        <button v-if="group.path" type="button" class="icon-button workspace-new"
          :title="t.sidebar.newIn(group.path)" :aria-label="t.sidebar.newIn(group.path)"
          @click="$emit('new', group)">
          <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
        </button>
      </section>
    </nav>

  </aside>
</template>
