import { useEffect, useState } from "react";
import { api } from "../api";
import type { Approval, Draft, Task } from "../types";
import { LedgerBadge } from "./LedgerBadge";

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function TaskDetail({ taskId }: { taskId: number }) {
  const [task, setTask] = useState<Task | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    setTask(null);
    Promise.all([api.getTask(taskId), api.getDrafts(taskId), api.getApprovals(taskId)])
      .then(([t, d, a]) => {
        if (!alive) return;
        setTask(t);
        setDrafts(d);
        setApprovals(a);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
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
      {drafts.length === 0 && <p className="muted">No drafts yet.</p>}
      {drafts.map((d) => (
        <details key={d.id} className="draft">
          <summary>
            v{d.version} <span className="muted small">{formatTime(d.created_at)}</span>
          </summary>
          <pre>{d.content}</pre>
        </details>
      ))}

      <h3>Approval ledger ({approvals.length})</h3>
      {approvals.length === 0 && <p className="muted">No approvals recorded.</p>}
      <ol className="timeline">
        {approvals.map((a) => (
          <li key={a.id} className={`event event-${a.action}`}>
            <span className="event-action">{a.action}</span>
            <span className="muted small"> · actor #{a.reviewer_actor_id ?? "—"} · {formatTime(a.created_at)}</span>
            {a.comment && <div className="event-comment">{a.comment}</div>}
          </li>
        ))}
      </ol>
    </section>
  );
}
