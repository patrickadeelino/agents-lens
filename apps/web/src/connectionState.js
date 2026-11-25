// Identifiers for the event stream's connection state. Components compare these
// identifiers; the visible text comes from connectionLabel() and may change freely.
export const CONNECTION = Object.freeze({
  idle: "idle",
  loading: "loading",
  receiving: "receiving",
  reconnecting: "reconnecting",
  interrupted: "interrupted",
  ended: "ended",
});

const labels = {
  [CONNECTION.idle]: "Inativa",
  [CONNECTION.loading]: "Carregando histórico",
  [CONNECTION.receiving]: "Ao vivo",
  [CONNECTION.reconnecting]: "Reconectando…",
  [CONNECTION.interrupted]: "Interrompida",
  [CONNECTION.ended]: "Encerrado",
};

export function connectionLabel(state) {
  return labels[state] ?? state;
}

export function isDisrupted(state) {
  return state === CONNECTION.reconnecting || state === CONNECTION.interrupted;
}
