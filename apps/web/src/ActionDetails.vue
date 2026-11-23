<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  entry: { type: Object, required: true },
});

const input = computed(() => props.entry.command?.command ?? props.entry.tool?.input);
const status = computed(() => (props.entry.command ?? props.entry.tool).status);
const error = computed(() => props.entry.tool?.error);
const output = computed(() => props.entry.command?.output ?? props.entry.tool?.output);
const capturedText = computed(() => [error.value, output.value]
  .filter((value) => value != null && value !== "")
  .map(displayValue).join("\n\n"));
const preview = computed(() => capturedText.value.slice(0, 2000).split("\n").slice(0, 15).join("\n"));
const jsonOpen = ref(false);
const copyStatus = ref("");

function displayValue(value) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

async function copyOutput() {
  try {
    await navigator.clipboard.writeText(capturedText.value);
    copyStatus.value = "Saída copiada.";
  } catch {
    copyStatus.value = "Não foi possível copiar a saída.";
  }
}
</script>

<template>
  <div class="command-detail">
    <dl class="command-metadata">
      <div v-if="entry.startedOffset != null || entry.offset != null">
        <dt>{{ entry.startedOffset != null ? "Início observado" : "Primeira observação" }}</dt>
        <dd>{{ entry.startedOffset ?? entry.offset }}</dd>
      </div>
      <div v-if="entry.command"><dt>Duração observada</dt><dd>{{ entry.duration ?? "Indisponível" }}</dd></div>
      <div v-if="entry.command"><dt>Código de saída</dt><dd>{{ entry.command.exitCode ?? "Não informado" }}</dd></div>
    </dl>
    <section class="action-section" aria-label="Entrada">
      <h3>Entrada</h3>
      <pre v-if="input != null" tabindex="0" aria-label="Entrada da ação">{{ displayValue(input) }}</pre>
      <p v-else>Entrada indisponível.</p>
    </section>
    <section class="action-section" aria-label="Saída">
      <h3>Saída capturada</h3>
      <p v-if="status === 'in_progress'">
        {{ capturedText ? "Saída parcial. A ação está em andamento." : "Aguardando saída." }}
      </p>
      <template v-if="preview !== capturedText">
        <details class="long-output">
          <summary>Saída completa</summary>
          <pre tabindex="0" aria-label="Saída capturada">{{ capturedText }}</pre>
        </details>
        <pre class="output-preview" tabindex="0" aria-label="Prévia da saída">{{ preview }}…</pre>
      </template>
      <pre v-else-if="capturedText" tabindex="0" aria-label="Saída capturada">{{ capturedText }}</pre>
      <p v-else-if="status === 'result_unconfirmed'">O app não recebeu o resultado desta ação.</p>
      <p v-else-if="status !== 'in_progress'">{{ output === "" ? "Saída vazia." : "Saída indisponível." }}</p>
      <div v-if="capturedText" class="inspector-actions">
        <button type="button" class="link-button" @click="copyOutput">Copiar saída</button>
        <span role="status">{{ copyStatus }}</span>
      </div>
    </section>
    <details class="action-technical">
      <summary>Detalhes técnicos</summary>
      <p v-if="entry.completedOffset != null">Fim observado {{ entry.completedOffset }}</p>
      <p v-if="entry.command?.startMissing">Início ausente dos eventos exibidos.</p>
      <p v-if="(entry.command ?? entry.tool).conflictingResults">Foram recebidos resultados divergentes para esta ação.</p>
      <details class="action-json" @toggle="jsonOpen = $event.currentTarget.open">
        <summary>Ver eventos JSON</summary>
        <pre v-if="jsonOpen" tabindex="0" aria-label="Eventos JSON da ação">{{ JSON.stringify(entry.command?.records ?? entry.records, null, 2) }}</pre>
      </details>
    </details>
    <p v-if="entry.duration" class="measurement-note">A duração observada é o intervalo entre a chegada dos eventos de início e de conclusão.</p>
  </div>
</template>
