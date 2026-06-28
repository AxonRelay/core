import type { Task, TaskStatus } from "../types";

const STATUSES: (TaskStatus | "all")[] = [
  "all",
  "draft",
  "waiting_review",
  "waiting_approval",
  "needs_revision",
  "approved",
  "completed",
  "rejected",
  "cancelled",
];

interface Props {
  tasks: Task[];
  selectedId: number | null;
  statusFilter: TaskStatus | "all";
  onSelect: (id: number) => void;
  onFilter: (status: TaskStatus | "all") => void;
}

export function TaskList({ tasks, selectedId, statusFilter, onSelect, onFilter }: Props) {
  return (
    <aside className="task-list">
      <div className="filter-row">
        <label htmlFor="status">Status</label>
        <select
          id="status"
          value={statusFilter}
          onChange={(e) => onFilter(e.target.value as TaskStatus | "all")}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      {tasks.length === 0 && <p className="muted">No tasks.</p>}
      <ul>
        {tasks.map((t) => (
          <li key={t.id}>
            <button
              className={t.id === selectedId ? "task-item selected" : "task-item"}
              onClick={() => onSelect(t.id)}
            >
              <span className="task-title">#{t.id} {t.title}</span>
              <span className={`status status-${t.status}`}>{t.status}</span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
