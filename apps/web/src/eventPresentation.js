const numbers = new Intl.NumberFormat("pt-BR");
const decimals = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const eventLabels = new Map([
  ["thread.started", "Sessão iniciada"],
  ["turn.started", "Processamento do prompt iniciado"],
  ["turn.completed", "Processamento do prompt concluído"],
  ["turn.failed", "Processamento do prompt falhou"],
  ["item.started", "Atividade iniciada"],
  ["item.updated", "Atividade atualizada"],
  ["item.completed", "Atividade encerrada"],
  ["error", "Erro do Codex"],
]);
const itemLabels = new Map([
  ["agent_message", "Mensagem do Codex"],
  ["reasoning", "Resumo de raciocínio"],
  ["todo_list", "Lista de tarefas"],
  ["error", "Diagnóstico do Codex"],
]);
const lifecycleEvents = new Set(["thread.started", "turn.started", "turn.completed"]);
const statuses = new Map([
  ["in_progress", ["◌", "Em andamento"]],
  ["completed", ["✓", "Concluído"]],
  ["failed", ["×", "Falhou"]],
  ["declined", ["×", "Recusado"]],
  ["result_unconfirmed", ["?", "Não confirmado"]],
]);
const tokenLabels = new Map([
  ["input_tokens", "Entrada"],
  ["output_tokens", "Saída"],
  ["cached_input_tokens", "Entrada em cache"],
  ["cache_write_input_tokens", "Entrada gravada em cache"],
  ["reasoning_output_tokens", "Raciocínio na saída"],
]);
const collaborationLabels = new Map([
  ["spawn_agent", "Iniciar subagente"],
  ["send_input", "Enviar mensagem ao subagente"],
  ["wait", "Aguardar subagente"],
  ["close_agent", "Encerrar subagente"],
  ["resume_agent", "Retomar subagente"],
]);

export function formatDuration(ns) {
  if (!Number.isFinite(ns) || ns < 0) return null;
  if (ns === 0) return "0 ms";
  if (ns < 1_000_000) return ns < 1_000 ? `${numbers.format(ns)} ns` : `${decimals.format(ns / 1_000)} µs`;
  if (ns < 999_950_000) return `${decimals.format(ns / 1_000_000)} ms`;
  if (ns < 59_950_000_000) return `${decimals.format(ns / 1_000_000_000)} s`;
  const seconds = Math.round(ns / 1_000_000_000);
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${numbers.format(minutes)} min ${remainder} s` : `${numbers.format(minutes)} min`;
}

export function formatOffset(ns) {
  const time = formatDuration(ns);
  return time === null ? null : `+${time}`;
}

export function statusPresentation(status) {
  const [icon, label] = statuses.get(status) ?? statuses.get("result_unconfirmed");
  return { icon, label, failed: status === "failed" || status === "declined" };
}

export function firstLine(text) {
  if (typeof text !== "string") return null;
  const [line] = text.trimStart().split(/\r?\n/, 1);
  return line.length > 160 ? `${line.slice(0, 160)}…` : line || null;
}

export function errorMessage(event) {
  const message = event.error?.message ?? event.message;
  return typeof message === "string" && message.trim() ? message : null;
}

export function describeEvent(event) {
  const { item, type } = event;
  const label = itemLabels.get(item?.type) ?? eventLabels.get(type) ?? "Evento do Codex";
  const markdown = item?.type === "agent_message" && typeof item.text === "string" ? item.text : null;
  const failed = type === "turn.failed" || type === "error";
  const text = failed ? errorMessage(event) : item?.message ?? item?.text;
  const tasks = item?.type === "todo_list" && Array.isArray(item.items)
    ? item.items.filter((task) => typeof task?.text === "string")
    : [];
  const technical = !failed && (lifecycleEvents.has(type) || item?.type === "error");
  return { label, markdown, text: !markdown && typeof text === "string" ? text : null, tasks, failed, technical };
}

export function trajectoryRows(rows, view) {
  if (view === "all") return rows;
  if (view === "failures") return rows.filter((entry) => entry.failed);

  const visible = [];
  for (const entry of rows) {
    if (!entry.technical) {
      visible.push(entry);
      continue;
    }
    const group = visible.at(-1);
    if (group?.technicalEvents) group.technicalEvents.push(entry);
    else visible.push({ key: `technical:${entry.key}`, technicalEvents: [entry] });
  }
  return visible;
}

export function commandTitle(command) {
  if (typeof command !== "string") return null;
  // Interpret only exact forms with no shell expansion or secondary command.
  const shell = /^(?:\/bin\/)?(?:bash|zsh|sh) -lc '([^']+)'$/.exec(command);
  const simpleCommand = shell?.[1] ?? command;
  const read = /^cat (?:-- )?([A-Za-z0-9_./][A-Za-z0-9_./-]*)$/.exec(simpleCommand);
  if (read) return `Ler ${read[1].split("/").filter(Boolean).at(-1) ?? read[1]}`;
  if (simpleCommand === "ls") return "Listar arquivos";
  if (simpleCommand === "ls -a") return "Listar arquivos, incluindo ocultos";
  return null;
}

export function reportedTokens(event) {
  if (event.type !== "turn.completed") return [];
  return Array.from(tokenLabels, ([field, label]) => ({ field, label, value: event.usage?.[field] }))
    .filter(({ value }) => Number.isSafeInteger(value) && value >= 0)
    .map(({ field, label, value }) => ({ field, label, value: numbers.format(value) }));
}

export function describeTool({ event }, previous) {
  const { item, type } = event;
  if (!item || !["item.started", "item.updated", "item.completed"].includes(type)) return null;

  let fields;
  switch (item.type) {
    case "mcp_tool_call":
      fields = {
        name: [item.server, item.tool].filter(Boolean).join(" / "),
        input: item.arguments,
        output: item.result,
      };
      break;
    case "web_search":
      fields = {
        name: "Busca na web",
        input: item.action ?? item.query,
        preview: item.query || null,
        output: item.results,
      };
      break;
    case "file_change":
      fields = {
        name: "Alteração de arquivos",
        input: item.changes,
        preview: item.changes?.[0]?.path,
      };
      break;
    case "collab_tool_call":
      fields = {
        name: collaborationLabels.get(item.tool) ?? item.tool ?? "Interação com subagente",
        input: item.prompt,
        output: item.agents_states,
      };
      break;
    default:
      return null;
  }

  const terminal = type === "item.completed";
  if (previous?.terminal && !terminal) return previous;
  const observedStatus = item.status ?? (item.type === "web_search" ? "completed" : "result_unconfirmed");
  const status = terminal ? observedStatus : item.status ?? "in_progress";
  const output = fields.output ?? (previous?.terminal ? null : previous?.output);
  const error = item.error?.message ?? (previous?.terminal ? null : previous?.error);
  const conflictingResults = previous?.conflictingResults || (previous?.terminal && (
    status !== previous.status || error !== previous.error
    || (output != null && previous.output != null && JSON.stringify(output) !== JSON.stringify(previous.output))
  ));
  return {
    name: fields.name || previous?.name || "Ferramenta MCP",
    input: fields.input ?? previous?.input,
    output,
    error: error ?? null,
    preview: fields.preview ?? previous?.preview,
    terminal,
    status,
    conflictingResults: Boolean(conflictingResults),
  };
}
