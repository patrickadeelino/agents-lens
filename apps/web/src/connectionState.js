import { t } from "./messages.js";

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

export function connectionLabel(state) {
  return Object.values(CONNECTION).includes(state) ? t.connection[state] : state;
}

export function isDisrupted(state) {
  return state === CONNECTION.reconnecting || state === CONNECTION.interrupted;
}
