<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { childPath, folderTitle, listFolders, parentPath, selectFolder } from "./folders.js";
import { t } from "./messages.js";

const emit = defineEmits(["select", "close", "unavailable"]);
const dialog = ref(null);
const listing = ref(null);
const loading = ref(false);
const selecting = ref(false);
const error = ref("");
let request;

function report(failure) {
  error.value = failure.message;
  if (failure.status === 503) emit("unavailable");
}

async function open(path) {
  request?.abort();
  const current = new AbortController();
  request = current;
  loading.value = true;
  error.value = "";
  try {
    const result = await listFolders(path, current.signal);
    if (!current.signal.aborted) listing.value = result;
  } catch (failure) {
    if (!current.signal.aborted) report(failure);
  } finally {
    if (request === current) loading.value = false;
  }
}

async function choose() {
  selecting.value = true;
  error.value = "";
  try {
    const selection = await selectFolder(listing.value.path);
    if (!request?.signal.aborted) emit("select", selection);
  } catch (failure) {
    report(failure);
  } finally {
    selecting.value = false;
  }
}

onMounted(() => {
  dialog.value.showModal();
  open("");
});
onUnmounted(() => request?.abort());
</script>

<template>
  <dialog ref="dialog" class="folder-picker" aria-labelledby="folder-picker-title"
    @cancel.prevent="$emit('close')" @keydown.esc.stop>
    <header>
      <h2 id="folder-picker-title">{{ t.folders.title }}</h2>
      <p v-if="listing" class="folder-current">{{ folderTitle(listing.root, listing.path) }}</p>
    </header>
    <button v-if="listing?.path" type="button" class="link-button folder-up" :disabled="loading"
      @click="open(parentPath(listing.path))">{{ t.folders.up }}</button>
    <p v-if="loading" class="quiet-text" role="status">{{ t.folders.loading }}</p>
    <ul v-else-if="listing?.folders.length" class="folder-list">
      <li v-for="name in listing.folders" :key="name">
        <button type="button" class="secondary" @click="open(childPath(listing.path, name))">{{ name }}</button>
      </li>
    </ul>
    <p v-else-if="listing" class="quiet-text">{{ t.folders.empty }}</p>
    <p v-if="listing?.truncated" class="quiet-text">{{ t.folders.truncated }}</p>
    <p v-if="error" class="composer-status" role="alert">
      {{ error }} <button type="button" class="link-button" @click="open(listing?.path ?? '')">{{ t.common.tryAgain }}</button>
    </p>
    <footer>
      <button type="button" class="secondary" @click="$emit('close')">{{ t.folders.cancel }}</button>
      <button type="button" :disabled="!listing || loading || selecting" @click="choose">{{ t.folders.use }}</button>
    </footer>
  </dialog>
</template>
