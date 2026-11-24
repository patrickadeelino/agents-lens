<script setup>
import { ref, useId } from "vue";
import ActionDetails from "./ActionDetails.vue";
import ActionRow from "./ActionRow.vue";
import CommandBlock from "./CommandBlock.vue";
import EventRow from "./EventRow.vue";

defineProps({
  entry: { type: Object, required: true },
  selected: Boolean,
  terminal: Boolean,
  workspace: String,
  runId: String,
});
defineEmits(["select", "announce"]);
const detailsId = useId();
const commandBlock = ref(null);
</script>

<template>
  <ActionRow
    v-if="entry.command || entry.tool" :entry="entry" :selected="selected" :controls="detailsId"
    :terminal="terminal" @select="action => $emit('select', action)"
  />
  <EventRow v-else :entry="entry" :selected="selected" :controls="detailsId" @select="action => $emit('select', action)" />
  <div v-show="selected" :id="detailsId" class="trajectory-action-details">
    <template v-if="selected">
      <CommandBlock v-if="entry.command?.command" ref="commandBlock" preview-only :raw="entry.command.command" :workspace="workspace" @announce="$emit('announce', $event)" />
      <ActionDetails :entry="entry" :terminal="terminal" :workspace="workspace" :run-id="runId" @announce="$emit('announce', $event)">
        <template v-if="entry.command?.command" #controls>
          <button type="button" class="link-button" @click="commandBlock?.copyCommand()">Copiar comando</button>
        </template>
      </ActionDetails>
    </template>
  </div>
</template>
