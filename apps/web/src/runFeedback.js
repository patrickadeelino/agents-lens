import { onUnmounted, ref, watch } from "vue";
import { CONNECTION } from "./connectionState.js";
import { displayCommand, maskPaths } from "./eventPresentation.js";
import { t } from "./messages.js";

export function useRunFeedback(connection) {
  const announcement = ref("");
  const lastUpdateSeconds = ref(null);
  const recovered = ref(null);
  let lastRecordAt = null;
  let recovering = false;
  let queue = [];
  let announceTimer;
  let ageTimer;
  let recoveredTimer;

  function announce(message) {
    if (queue.length < 3 && !queue.includes(message)) {
      queue.push(message);
      if (announcement.value === message) announcement.value = "";
    }
    if (announceTimer) return;
    announceTimer = setTimeout(() => {
      announcement.value = queue.join(" ");
      announceTimer = undefined;
      queue = [];
    }, 1500);
  }

  function refreshAge() {
    lastUpdateSeconds.value = lastRecordAt == null ? null : Math.floor((Date.now() - lastRecordAt) / 1000);
  }

  watch(connection, (current, previous) => {
    clearInterval(ageTimer);
    if (current === CONNECTION.reconnecting) {
      recovering = false;
      recovered.value = null;
      refreshAge();
      ageTimer = setInterval(refreshAge, 1000);
      announce(t.connection.lost);
    } else if (previous === CONNECTION.reconnecting && current === CONNECTION.receiving) {
      recovering = true;
      recovered.value = 0;
      announce(t.connection.reconnected);
      clearTimeout(recoveredTimer);
      recoveredTimer = setTimeout(() => {
        recovered.value = null;
        recovering = false;
      }, 5000);
    } else if (current === CONNECTION.interrupted) {
      recovered.value = null;
      recovering = false;
      announce(t.connection.streamInterrupted);
    }
  }, { flush: "sync" });

  function observe(record, workspace) {
    lastRecordAt = Date.now();
    if (recovering) recovered.value++;
    const { event } = record;
    if (event.type === "item.completed" && event.item?.status === "failed") {
      const name = maskPaths(displayCommand(event.item.command) || event.item.tool, workspace);
      const describe = event.item.command ? t.announce.commandFailed : t.announce.actionFailed;
      const failure = name ? describe(name.slice(0, 100)) : t.announce.unnamedFailure;
      announce(failure);
    }
  }

  function reset() {
    clearTimeout(announceTimer);
    clearTimeout(recoveredTimer);
    clearInterval(ageTimer);
    announceTimer = undefined;
    queue = [];
    announcement.value = "";
    lastRecordAt = null;
    recovering = false;
    recovered.value = null;
    lastUpdateSeconds.value = null;
  }

  onUnmounted(reset);
  return { announce, announcement, lastUpdateSeconds, recovered, observe, reset };
}
