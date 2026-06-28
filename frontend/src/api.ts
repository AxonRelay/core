import type { AgentDefinition, Approval, Draft, LedgerVerdict, Task, TaskStatus } from "./types";

// Read-only REST client. Base defaults to "/api" (reverse-proxied to the backend);
// override with VITE_API_BASE for other setups.
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" }, signal });
  if (!res.ok) {
    if (res.status === 429) {
      throw new Error("Rate limited (429) — slow down and retry.");
    }
    // The backend reports errors as {"detail": "..."}; surface it when present.
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON body; keep statusText
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listTasks: (status?: TaskStatus, signal?: AbortSignal) =>
    get<Task[]>(`/tasks${status ? `?status=${status}` : ""}`, signal),
  getTask: (id: number, signal?: AbortSignal) => get<Task>(`/tasks/${id}`, signal),
  getDrafts: (id: number, signal?: AbortSignal) => get<Draft[]>(`/tasks/${id}/drafts`, signal),
  getApprovals: (id: number, signal?: AbortSignal) => get<Approval[]>(`/tasks/${id}/approvals`, signal),
  verifyLedger: (id: number, signal?: AbortSignal) => get<LedgerVerdict>(`/tasks/${id}/ledger/verify`, signal),
  listAgents: (signal?: AbortSignal) => get<AgentDefinition[]>(`/agents`, signal),
};
