<script setup>
import { threadLink, workspaceName } from "./conversationNavigation.js";
import { t } from "./messages.js";

defineProps({ threads: { type: Array, required: true } });
defineEmits(["open"]);
</script>

<template>
  <section class="home-composer" aria-labelledby="composer-title">
    <p class="view-caption">{{ t.demo.caption }}</p>
    <h1 id="composer-title">{{ t.demo.heading }}</h1>
    <p class="demo-lead">{{ t.demo.lead }}</p>
    <ul class="demo-list">
      <li v-for="thread in threads" :key="thread.root_run_id">
        <a class="demo-card" :href="threadLink(thread)" @click="$emit('open', $event)">
          <strong>{{ thread.title }}</strong>
          <span v-if="thread.preview && thread.preview !== thread.title">{{ thread.preview }}</span>
          <small v-if="thread.workspace">{{ workspaceName(thread.workspace) }}</small>
          <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 12h15m-6-6 6 6-6 6"/></svg>
        </a>
      </li>
    </ul>
  </section>
</template>
