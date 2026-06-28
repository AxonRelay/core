// Mirror of the AxonRelay REST payloads (see backend/app/schema.py & serializers).

export type TaskStatus =
  | "draft"
  | "waiting_review"
  | "waiting_approval"
  | "approved"
  | "rejected"
  | "needs_revision"
  | "completed"
  | "cancelled";

export interface Actor {
  id: number;
  type: "human" | "ai";
  name: string;
}

export interface Assignment {
  id: number;
  task_id: number;
  actor_id: number;
  role: "executor" | "reviewer" | "approver" | "observer";
  actor?: Actor | null;
}

export interface Task {
  id: number;
  thread_id: string;
  title: string;
  description: string | null;
  status: TaskStatus;
  current_draft: string | null;
  feedback: string | null;
  creator_actor_id: number | null;
  created_at: string;
  updated_at: string;
  assignments?: Assignment[];
}

export interface Draft {
  id: number;
  task_id: number;
  version: number;
  content: string;
  created_at: string;
}

export type ApprovalAction = "approved" | "rejected";

export interface Approval {
  id: number;
  task_id: number;
  reviewer_actor_id: number | null;
  action: ApprovalAction;
  comment: string | null;
  created_at: string;
}

export interface AgentDefinition {
  id: number;
  actor_id: number;
  agent_type: string;
  description: string | null;
  is_active: boolean;
  actor?: Actor | null;
}

export interface LedgerVerdict {
  valid: boolean;
  broken_at: number | null;
  count: number;
  legacy: number;
}
