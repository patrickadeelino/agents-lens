<script setup>
import { computed } from "vue";
import { branchStateLabel, visibleTrajectoryRows } from "./eventPresentation.js";
import TechnicalEvents from "./TechnicalEvents.vue";
import TrajectoryEntry from "./TrajectoryEntry.vue";
import { t } from "./messages.js";

const props = defineProps({
  rows: Array, filter: String, selected: String, terminal: Boolean,
  workspace: String, runId: String, isOpen: Function,
});
const emit = defineEmits(["select", "announce", "toggle"]);
const visible = computed(() => visibleTrajectoryRows(props.rows, props.filter));

function toggle(key, event) {
  const open = event.currentTarget.open;
  if (open !== props.isOpen(key)) emit("toggle", { key, open });
}
</script>

<template>
  <ol class="event-list">
    <li v-for="entry in visible" :key="entry.key"
      :data-entry-key="entry.key" :data-command-id="entry.command?.id" :data-command-status="(entry.command ?? entry.tool)?.status"
      :data-failure="entry.failed || undefined" :data-event-type="entry.event?.type"
      :class="{ 'trajectory-action': entry.command || entry.tool, 'trajectory-message': entry.markdown || entry.omitted || entry.event?.item?.type === 'reasoning' }">
      <details v-if="entry.branch" class="delegation-branch" :open="isOpen(entry.key)"
        @toggle="toggle(entry.key, $event)">
        <summary>
          <span>{{ entry.label }}</span> <span class="delegation-state">{{ branchStateLabel(entry.state) }}</span>
          <span v-if="entry.summary" class="delegation-result">{{ entry.summary }}</span>
        </summary>
        <TrajectoryList :rows="entry.rows" :filter="filter" :selected="selected" :terminal="entry.terminal"
          :workspace="workspace" :run-id="runId" :is-open="isOpen"
          @toggle="$emit('toggle', $event)" @select="$emit('select', $event)" @announce="$emit('announce', $event)" />
        <p v-if="!entry.rows.length" class="delegation-summary">
          {{ entry.terminal ? t.delegation.noActivity : t.delegation.waiting }}
        </p>
      </details>
      <TechnicalEvents v-else-if="entry.technicalEvents" :events="entry.technicalEvents" :title="entry.groupTitle">
        <template #entry="{ entry: item }">
          <TrajectoryEntry :entry="item" :selected="selected === item.key" :terminal="entry.terminal ?? terminal" :workspace="workspace"
            :run-id="runId" @select="$emit('select', $event)" @announce="$emit('announce', $event)" />
        </template>
      </TechnicalEvents>
      <TrajectoryEntry v-else :entry="entry" :selected="selected === entry.key" :terminal="entry.terminal ?? terminal" :workspace="workspace"
        :run-id="runId" @select="$emit('select', $event)" @announce="$emit('announce', $event)" />
    </li>
  </ol>
</template>
