<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue";
import { loadRunHistory } from "./runs.js";
import { savedOutcome } from "./trajectoryProjection.js";
import { useRunTrajectory } from "./useRunTrajectory.js";
import TrajectoryPane from "./TrajectoryPane.vue";

defineOptions({ inheritAttrs: false });
const props = defineProps({ runId: { type: String, required: true }, status: String, threadId: String });
const emit = defineEmits(["observed"]);
const model = useRunTrajectory();
const loading = ref(true);
const error = ref("");
const detail = ref("");
let request;

async function load() {
  request?.abort();
  const current = new AbortController();
  request = current;
  loading.value = true;
  error.value = "";
  try {
    const page = await loadRunHistory(props.runId, current.signal);
    current.signal.throwIfAborted();
    model.initialize(page, props.threadId);
    const outcome = savedOutcome(page);
    detail.value = outcome.detail ?? (outcome.status === "failed" ? model.data.value.issue : "");
  } catch {
    if (!current.signal.aborted) error.value = "Não foi possível carregar a trajetória.";
  } finally {
    if (request === current && !current.signal.aborted) loading.value = false;
  }
}

watch([model.data, model.nextBefore, loading], ([data, before, pending]) => {
  if (!pending && !error.value) emit("observed", props.runId, data, before);
});

onMounted(load);
onUnmounted(() => request?.abort());
</script>

<template>
  <TrajectoryPane v-bind="$attrs" :model="model" :run-id="runId" :status="status" :detail="detail"
    :loading="loading" :error="error" closeable @retry-history="load" />
</template>
