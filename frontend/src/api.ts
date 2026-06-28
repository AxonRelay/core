import type { AgentDefinition, Approval, Draft, LedgerVerdict, Task, TaskStatus } from "./types";

// Read-only REST client. Base defaults to "/api" (reverse-proxied to the backend);
// override with VITE_API_BASE for other setups.
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} for ${path}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listTasks: (status?: TaskStatus) => get<Task[]>(`/tasks${status ? `?status=${status}` : ""}`),
  getTask: (id: number) => get<Task>(`/tasks/${id}`),
  getDrafts: (id: number) => get<Draft[]>(`/tasks/${id}/drafts`),
  getApprovals: (id: number) => get<Approval[]>(`/tasks/${id}/approvals`),
  verifyLedger: (id: number) => get<LedgerVerdict>(`/tasks/${id}/ledger/verify`),
  listAgents: () => get<AgentDefinition[]>(`/agents`),
};
