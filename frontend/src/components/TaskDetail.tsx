import { useEffect, useState } from "react";
import { api, isAbort } from "../api";
import type { Approval, Draft, Task } from "../types";
import { LedgerBadge } from "./LedgerBadge";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function TaskDetail({ taskId }: { taskId: number }) {
  const [task, setTask] = useState<Task | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [draftsError, setDraftsError] = useState<string | null>(null);
  const [approvalsError, setApprovalsError] = useState<string | null>(null);

  // Each section fetches independently so a single failed sub-request doesn't
  // blank the whole pane (the task header still renders if drafts/approvals fail).
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    setError(null);
    setTask(null);
    setDrafts([]);
    setDraftsError(null);
    setApprovals([]);
    setApprovalsError(null);

    api
      .getTask(taskId, controller.signal)
      .then((t) => !cancelled && setTask(t))
      .catch((e: unknown) => !cancelled && !isAbort(e) && setError(message(e)));
    api
      .getDrafts(taskId, controller.signal)
      .then((d) => !cancelled && setDrafts(d))
      .catch((e: unknown) => !cancelled && !isAbort(e) && setDraftsError(message(e)));
    api
      .getApprovals(taskId, controller.signal)
      .then((a) => !cancelled && setApprovals(a))
      .catch((e: unknown) => !cancelled && !isAbort(e) && setApprovalsError(message(e)));

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [taskId]);

  if (error) return <section className="detail"><p className="badge badge-warn">{error}</p></section>;
  if (!task) return <section className="detail"><p className="muted">Loading…</p></section>;

  return (
    <section className="detail">
      <header className="detail-head">
        <h2>#{task.id} {task.title}</h2>
        <div className="meta-row">
          <span className={`status status-${task.status}`}>{task.status}</span>
          <LedgerBadge taskId={task.id} />
        </div>
        {task.description && <p className="desc">{task.description}</p>}
        <p className="muted small">thread {task.thread_id} · created {formatTime(task.created_at)}</p>
      </header>

      <h3>Assignments</h3>
      {task.assignments && task.assignments.length > 0 ? (
        <ul className="assignments">
          {task.assignments.map((a) => (
            <li key={a.id}>
              <span className={`role role-${a.role}`}>{a.role}</span>{" "}
              {a.actor ? `${a.actor.name} (${a.actor.type})` : `actor #${a.actor_id}`}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No assignments.</p>
      )}

      <h3>Drafts ({drafts.length})</h3>
      {draftsError && <p className="badge badge-warn">{draftsError}</p>}
      {!draftsError && drafts.length === 0 && <p className="muted">No drafts yet.</p>}
      {drafts.map((d) => (
        <details key={d.id} className="draft">
          <summary>
            v{d.version} <span className="muted small">{formatTime(d.created_at)}</span>
            {d.commitment && (
              <span className="muted small" title={`${d.commitment_algorithm ?? "commitment"}: ${d.commitment}`}>
                {" "}
                · {d.commitment.slice(0, 12)}…
              </span>
            )}
          </summary>
          <pre>{d.content}</pre>
        </details>
      ))}

      <h3>Approval ledger ({approvals.length})</h3>
      {approvalsError && <p className="badge badge-warn">{approvalsError}</p>}
      {!approvalsError && approvals.length === 0 && <p className="muted">No approvals recorded.</p>}
      <ol className="timeline">
        {approvals.map((a) => (
          <li key={a.id} className={`event event-${a.action}`}>
            <span className="event-action">{a.action}</span>
            <span className="muted small"> · actor #{a.reviewer_actor_id ?? "—"} · {formatTime(a.created_at)}</span>
            {a.artifact_bound ? (
              <span
                className="muted small"
                title={`${a.artifact_ref ?? ""}\n${a.artifact_commitment_algorithm ?? "commitment"}: ${a.artifact_commitment ?? ""}`}
              >
                {" "}
                · draft v{a.artifact_version}
                {a.artifact_commitment && ` (${a.artifact_commitment.slice(0, 12)}…)`}
              </span>
            ) : (
              <span className="badge badge-warn small" title="Recorded before approvals named their artifact">
                not artifact-bound
              </span>
            )}
            {a.comment && <div className="event-comment">{a.comment}</div>}
          </li>
        ))}
      </ol>
    </section>
  );
}
