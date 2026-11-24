<script setup>
import { computed, ref, useId } from "vue";
import { commandStderr, displayValue } from "./eventPresentation.js";
import { renderAgentMarkdown } from "./markdown.js";

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
    copyStatus.value = "Saída copiada.";
  } catch {
    copyStatus.value = "Não foi possível copiar a saída.";
  }
  emit("announce", copyStatus.value);
}
</script>

<template>
  <div class="command-detail">
    <section v-if="input != null" class="action-section" aria-label="Entrada">
      <h3 class="run-kicker">Entrada</h3>
      <pre tabindex="0" aria-label="Entrada da ação">{{ formatValue(input) }}</pre>
    </section>
    <section v-if="entry.command || entry.tool" class="action-section" aria-label="Saída">
      <p v-if="status === 'in_progress'">
        {{ capturedText ? "Saída parcial. A ação está em andamento." : "Aguardando saída." }}
      </p>
      <p v-if="status === 'result_unconfirmed'">
        {{ !terminal ? "Resultado indisponível até o momento." : entry.command
          ? "A execução terminou sem enviar o resultado deste comando."
          : "A execução terminou sem enviar o resultado desta ação." }}
      </p>
      <pre v-if="capturedText" :id="outputId" class="inspector-code" tabindex="0" :aria-label="longOutput && !outputOpen ? 'Prévia da saída' : 'Saída capturada'">{{ longOutput && !outputOpen ? preview + "…" : capturedText }}</pre>
      <button v-if="longOutput" type="button" class="link-button output-toggle" :aria-controls="outputId" :aria-expanded="outputOpen" @click="outputOpen = !outputOpen">
        {{ outputOpen ? "Recolher saída" : "Mostrar saída completa" }}
      </button>
      <p v-if="!capturedText && status !== 'in_progress' && status !== 'result_unconfirmed'">
        {{ output === "" ? "Saída vazia." : "Saída indisponível." }}
      </p>
    </section>
    <section v-if="entry.markdown" class="action-section">
      <div v-if="message" class="agent-message" v-html="message"></div>
      <button v-else-if="final" type="button" class="link-button" @click="$emit('jump')">Ler a resposta final ↑</button>
    </section>
    <details class="action-technical">
      <summary>Detalhes</summary>
      <div class="inspector-actions">
        <slot name="controls" />
        <button v-if="capturedText" type="button" class="link-button" @click="copyOutput">Copiar saída</button>
        <details class="action-json" @toggle="jsonOpen = $event.currentTarget.open">
          <summary>Ver evento JSON</summary>
          <pre v-if="jsonOpen" tabindex="0" aria-label="JSON do evento">{{ formatValue(entry.command?.records ?? entry.records ?? entry.event) }}</pre>
        </details>
        <span>{{ copyStatus }}</span>
      </div>

    <dl v-if="entry.command || entry.tool" class="command-metadata">
      <div v-if="entry.command || entry.offset != null">
        <dt>{{ entry.command ? "Início observado" : "Primeira observação" }}</dt>
        <dd>{{ entry.command ? entry.startedOffset ?? "—" : entry.offset }}</dd>
      </div>
      <div v-if="entry.command"><dt>Duração observada</dt><dd>{{ entry.duration ?? "—" }}</dd></div>
      <div v-if="entry.command"><dt>Código de saída</dt><dd>{{ entry.command.exitCode ?? "não informado" }}</dd></div>
    </dl>

      <details v-if="entry.command?.command" class="action-json">
        <summary>Ver original</summary>
        <pre tabindex="0" aria-label="Comando original">{{ formatValue(entry.command.command) }}</pre>
      </details>
      <p v-if="entry.completedOffset != null">Fim observado {{ entry.completedOffset }}</p>
      <p v-if="itemId">ID do item <code>{{ itemId }}</code></p>
      <p v-if="runId">ID da execução <code>{{ runId }}</code></p>
      <p v-if="entry.command?.startMissing">Início ausente dos eventos exibidos.</p>
      <p v-if="(entry.command ?? entry.tool)?.conflictingResults">Foram recebidos resultados divergentes para esta ação.</p>
    <p v-if="entry.duration" class="measurement-note">A duração mede o intervalo entre a chegada dos eventos de início e de conclusão.</p>
    </details>
  </div>
</template>
