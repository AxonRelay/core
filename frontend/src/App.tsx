import { useEffect, useState } from "react";
import { api } from "./api";
import { TaskDetail } from "./components/TaskDetail";
import { TaskList } from "./components/TaskList";
import type { Task, TaskStatus } from "./types";

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<TaskStatus | "all">("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .listTasks(statusFilter === "all" ? undefined : statusFilter)
      .then((ts) => {
        if (!alive) return;
        setTasks(ts);
        setSelectedId((cur) => (cur && ts.some((t) => t.id === cur) ? cur : (ts[0]?.id ?? null)));
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [statusFilter]);

  return (
    <div className="app">
      <header className="app-head">
        <h1>AxonRelay</h1>
        <span className="muted">governance ledger · read-only</span>
      </header>
      {error && <p className="badge badge-warn app-error">{error}</p>}
      <div className="layout">
        <TaskList
          tasks={tasks}
          selectedId={selectedId}
          statusFilter={statusFilter}
          onSelect={setSelectedId}
          onFilter={setStatusFilter}
        />
        {selectedId != null ? (
          <TaskDetail taskId={selectedId} />
        ) : (
          <section className="detail">
            <p className="muted">Select a task.</p>
          </section>
        )}
      </div>
    </div>
  );
}
