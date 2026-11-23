<script setup>
import { computed, ref, useId } from "vue";
import { displayValue } from "./eventPresentation.js";

const props = defineProps({
  entry: { type: Object, required: true },
  terminal: Boolean,
  workspace: String,
});
const emit = defineEmits(["announce"]);

const input = computed(() => props.entry.tool?.input);
const status = computed(() => (props.entry.command ?? props.entry.tool)?.status);
const error = computed(() => props.entry.tool?.error);
const output = computed(() => props.entry.command?.output ?? props.entry.tool?.output);
const capturedText = computed(() => [error.value, output.value]
  .filter((value) => value != null && value !== "")
  .map(formatValue).join("\n\n"));
const preview = computed(() => capturedText.value.slice(0, 2000).split("\n").slice(0, 15).join("\n"));
const jsonOpen = ref(false);
const outputOpen = ref(false);
const outputId = useId();
const longOutput = computed(() => preview.value !== capturedText.value);
const copyStatus = ref("");

function formatValue(value) {
  return displayValue(value, props.workspace);
}

async function copyOutput() {
  try {
    await navigator.clipboard.writeText(capturedText.value);
    copyStatus.value = "Saída copiada.";
  } catch {
    copyStatus.value = "Não foi possível copiar a saída.";
  }
  emit("announce", copyStatus.value);
}
</script>

<template>
  <div class="command-detail">
    <dl v-if="entry.command || entry.tool" class="command-metadata">
      <div v-if="entry.command || entry.offset != null">
        <dt>{{ entry.command ? "Início observado" : "Primeira observação" }}</dt>
        <dd>{{ entry.command ? entry.startedOffset ?? "—" : entry.offset }}</dd>
      </div>
      <div v-if="entry.command"><dt>Duração observada</dt><dd>{{ entry.duration ?? "—" }}</dd></div>
      <div v-if="entry.command"><dt>Código de saída</dt><dd>{{ entry.command.exitCode ?? "não informado" }}</dd></div>
    </dl>
    <section v-if="input != null" class="action-section" aria-label="Entrada">
      <h3 class="run-kicker">Entrada</h3>
      <pre tabindex="0" aria-label="Entrada da ação">{{ formatValue(input) }}</pre>
    </section>
    <section v-if="entry.command || entry.tool" class="action-section" aria-label="Saída">
      <h3 class="run-kicker">Saída capturada</h3>
      <p v-if="status === 'in_progress'">
        {{ capturedText ? "Saída parcial. A ação está em andamento." : "Aguardando saída." }}
      </p>
      <p v-if="status === 'result_unconfirmed'">
        {{ !terminal ? "Resultado indisponível até o momento." : entry.command
          ? "A execução terminou sem enviar o resultado deste comando."
          : "A execução terminou sem enviar o resultado desta ação." }}
      </p>
      <pre v-if="capturedText" :id="outputId" tabindex="0" :aria-label="longOutput && !outputOpen ? 'Prévia da saída' : 'Saída capturada'">{{ longOutput && !outputOpen ? preview + "…" : capturedText }}</pre>
      <button v-if="longOutput" type="button" class="link-button output-toggle" :aria-controls="outputId" :aria-expanded="outputOpen" @click="outputOpen = !outputOpen">
        {{ outputOpen ? "Recolher" : "Mostrar tudo" }}
      </button>
      <p v-if="!capturedText && status !== 'in_progress' && status !== 'result_unconfirmed'">
        {{ output === "" ? "Saída vazia." : "Saída indisponível." }}
      </p>
    </section>
    <div class="inspector-actions">
      <button v-if="capturedText" type="button" class="link-button" @click="copyOutput">Copiar</button>
      <details class="action-json" @toggle="jsonOpen = $event.currentTarget.open">
        <summary>Ver evento JSON</summary>
        <pre v-if="jsonOpen" tabindex="0" aria-label="JSON do evento">{{ formatValue(entry.command?.records ?? entry.records ?? entry.event) }}</pre>
      </details>
      <span>{{ copyStatus }}</span>
    </div>
    <details v-if="entry.command || entry.tool" class="action-technical">
      <summary>Detalhes técnicos</summary>
      <template v-if="entry.command?.command">
        <p>Comando completo</p>
        <pre tabindex="0" aria-label="Comando completo">{{ formatValue(entry.command.command) }}</pre>
      </template>
      <p v-if="entry.completedOffset != null">Fim observado {{ entry.completedOffset }}</p>
      <p v-if="entry.command?.startMissing">Início ausente dos eventos exibidos.</p>
      <p v-if="(entry.command ?? entry.tool).conflictingResults">Foram recebidos resultados divergentes para esta ação.</p>
    </details>
    <p v-if="entry.duration" class="measurement-note">A duração mede o intervalo entre a chegada dos eventos de início e de conclusão.</p>
  </div>
</template>
