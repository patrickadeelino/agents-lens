<script setup>
import { computed, ref, useId } from "vue";
import { commandStderr, displayValue } from "./eventPresentation.js";
import { renderAgentMarkdown } from "./markdown.js";
import { t } from "./messages.js";

const props = defineProps({
  entry: { type: Object, required: true },
  terminal: Boolean,
  workspace: String,
  runId: String,
  final: Boolean,
});
const emit = defineEmits(["announce", "jump"]);

const input = computed(() => props.entry.tool?.input);
const status = computed(() => (props.entry.command ?? props.entry.tool)?.status);
const itemId = computed(() => props.entry.command?.id ?? props.entry.event?.item?.id);
const error = computed(() => props.entry.tool?.error ?? commandStderr(props.entry.command));
const output = computed(() => props.entry.command?.output ?? props.entry.tool?.output);
const capturedText = computed(() => [...new Set([error.value, output.value])]
  .filter((value) => value != null && value !== "")
  .map(formatValue).join("\n\n"));
const preview = computed(() => capturedText.value.slice(0, 2000).split("\n").slice(0, 15).join("\n"));
const jsonOpen = ref(false);
const outputOpen = ref(false);
const outputId = useId();
const longOutput = computed(() => preview.value !== capturedText.value);
const copyStatus = ref("");
const message = computed(() => props.entry.markdown && !props.final ? renderAgentMarkdown(props.entry.markdown) : "");

function formatValue(value) {
  return displayValue(value, props.workspace);
}

async function copyOutput() {
  try {
    await navigator.clipboard.writeText(capturedText.value);
    copyStatus.value = t.action.outputCopied;
  } catch {
    copyStatus.value = t.action.outputCopyFailed;
  }
  emit("announce", copyStatus.value);
}
</script>

<template>
  <div class="command-detail">
    <section v-if="input != null" class="action-section" :aria-label="t.action.input">
      <h3 class="run-kicker">{{ t.action.input }}</h3>
      <pre tabindex="0" :aria-label="t.action.inputLabel">{{ formatValue(input) }}</pre>
    </section>
    <section v-if="entry.command || entry.tool" class="action-section" :aria-label="t.action.output">
      <p v-if="status === 'in_progress'">
        {{ capturedText ? t.action.partialOutput : t.action.waitingOutput }}
      </p>
      <p v-if="status === 'result_unconfirmed'">
        {{ !terminal ? t.action.resultPending : entry.command
          ? t.action.commandNoResult
          : t.action.actionNoResult }}
      </p>
      <pre v-if="capturedText" :id="outputId" class="inspector-code" tabindex="0" :aria-label="longOutput && !outputOpen ? t.action.outputPreview : t.action.capturedOutput">{{ longOutput && !outputOpen ? preview + "…" : capturedText }}</pre>
      <button v-if="longOutput" type="button" class="link-button output-toggle" :aria-controls="outputId" :aria-expanded="outputOpen" @click="outputOpen = !outputOpen">
        {{ outputOpen ? t.action.collapseOutput : t.action.showOutput }}
      </button>
      <p v-if="!capturedText && status !== 'in_progress' && status !== 'result_unconfirmed'">
        {{ output === "" ? t.action.emptyOutput : t.action.noOutput }}
      </p>
    </section>
    <section v-if="entry.markdown" class="action-section">
      <div v-if="message" class="agent-message" v-html="message"></div>
      <button v-else-if="final" type="button" class="link-button" @click="$emit('jump')">{{ t.action.readFinal }}</button>
    </section>
    <details class="action-technical">
      <summary>{{ t.action.details }}</summary>
      <div class="inspector-actions">
        <slot name="controls" />
        <button v-if="capturedText" type="button" class="link-button" @click="copyOutput">{{ t.action.copyOutput }}</button>
        <details class="action-json" @toggle="jsonOpen = $event.currentTarget.open">
          <summary>{{ t.action.viewJson }}</summary>
          <pre v-if="jsonOpen" tabindex="0" :aria-label="t.action.jsonLabel">{{ formatValue(entry.command?.records ?? entry.records ?? entry.event) }}</pre>
        </details>
        <span>{{ copyStatus }}</span>
      </div>

    <dl v-if="entry.command || entry.tool" class="command-metadata">
      <div v-if="entry.command || entry.offset != null">
        <dt>{{ entry.command ? t.action.observedStart : t.action.firstSeen }}</dt>
        <dd>{{ entry.command ? entry.startedOffset ?? "—" : entry.offset }}</dd>
      </div>
      <div v-if="entry.command"><dt>{{ t.action.observedDuration }}</dt><dd>{{ entry.duration ?? "—" }}</dd></div>
      <div v-if="entry.command"><dt>{{ t.action.exitCode }}</dt><dd>{{ entry.command.exitCode ?? t.action.notReported }}</dd></div>
    </dl>

      <details v-if="entry.command?.command" class="action-json">
        <summary>{{ t.action.viewOriginal }}</summary>
        <pre tabindex="0" :aria-label="t.action.originalCommand">{{ formatValue(entry.command.command) }}</pre>
      </details>
      <p v-if="entry.completedOffset != null">{{ t.action.observedEnd(entry.completedOffset) }}</p>
      <p v-if="itemId">{{ t.action.itemId }} <code>{{ itemId }}</code></p>
      <p v-if="runId">{{ t.action.runId }} <code>{{ runId }}</code></p>
      <p v-if="entry.command?.startMissing">{{ t.action.startMissing }}</p>
      <p v-if="(entry.command ?? entry.tool)?.conflictingResults">{{ t.action.conflictingDetail }}</p>
    <p v-if="entry.duration" class="measurement-note">{{ t.action.measurementNote }}</p>
    </details>
  </div>
</template>
