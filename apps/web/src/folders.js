// Requests and path helpers for choosing a folder below the executor's workspace root.
import { t } from "./messages.js";

export const childPath = (path, name) => (path ? `${path}/${name}` : name);

export const parentPath = (path) => path.split("/").slice(0, -1).join("/");

export const folderTitle = (root, path) => (path ? `${root.replace(/\/$/, "")}/${path}` : root);

export class FolderError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function read(url, options, fallback) {
  const response = await fetch(url, { cache: "no-store", ...options }).catch(() => null);
  if (!response) throw new FolderError(fallback);
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload) {
    const detail = typeof payload?.detail === "string" ? payload.detail : fallback;
    throw new FolderError(detail, response.status);
  }
  return payload;
}

export function listFolders(path, signal) {
  return read(`/folders?${new URLSearchParams({ path })}`, { signal }, t.folders.loadFailed);
}

export function selectFolder(path) {
  return read("/folders", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Agent-Lens": "folder-selection" },
    body: JSON.stringify({ path }),
  }, t.folders.selectFailed);
}
