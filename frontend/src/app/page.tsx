"use client";

import { useState, useEffect } from "react";

type StatusResponse = {
  thread_id: string;
  status: string;
  current_draft: string | null;
  next_action: string;
};

export default function Dashboard() {
  const [task, setTask] = useState("");
  const [threadId, setThreadId] = useState("");
  const [statusData, setStatusData] = useState<StatusResponse | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [loading, setLoading] = useState(false);

  const API_URL = "/api";

  const startTask = async () => {
    if (!task) return;
    setLoading(true);
    const newThreadId = "thread-" + Math.random().toString(36).substr(2, 9);
    setThreadId(newThreadId);
    await fetch(`${API_URL}/task/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, thread_id: newThreadId }),
    });
    setLoading(false);
  };

  useEffect(() => {
    if (!threadId) return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/task/${threadId}`);
        if (res.ok) {
          const data: StatusResponse = await res.json();
          setStatusData(data);
          if (data.status === "waiting_approval" && !editDraft) {
            setEditDraft(data.current_draft || "");
          }
        }
      } catch (e) {
        console.error("Polling error", e);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [threadId, editDraft]);

  const approveTask = async () => {
    if (!threadId) return;
    setLoading(true);
    await fetch(`${API_URL}/task/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        approved: true,
        modified_draft: editDraft,
      }),
    });
    setEditDraft("");
    setLoading(false);
  };

  const resetTask = () => {
    setTask("");
    setThreadId("");
    setStatusData(null);
    setEditDraft("");
  };

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 p-8 font-mono">
      <div className="max-w-2xl mx-auto">
        <header className="mb-8 border-b border-slate-700 pb-4">
          <h1 className="text-3xl font-bold text-blue-400">AxonRelay</h1>
          <p className="text-slate-400 text-sm">
            Human-in-the-Loop Agent Orchestrator
          </p>
        </header>

        {/* Task input */}
        <div className="bg-slate-800 p-6 rounded-lg mb-6">
          <label className="block mb-2 font-semibold text-sm">
            Task for AI
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              className="flex-1 bg-slate-700 border border-slate-600 rounded p-2 text-white placeholder-slate-400"
              placeholder="e.g. Write an apology email to the client"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              disabled={!!threadId}
              onKeyDown={(e) => e.key === "Enter" && !threadId && startTask()}
            />
            <button
              onClick={startTask}
              disabled={loading || !!threadId}
              className="bg-blue-600 hover:bg-blue-500 px-6 py-2 rounded font-bold disabled:opacity-50 transition"
            >
              Start
            </button>
          </div>
        </div>

        {/* Status display */}
        {statusData && (
          <div className="bg-slate-800 p-6 rounded-lg border border-slate-700">
            <div className="flex justify-between items-center mb-4">
              <span className="text-sm text-slate-400">
                ID: {threadId}
              </span>
              <span
                className={`px-3 py-1 rounded-full text-xs font-bold ${
                  statusData.status === "completed"
                    ? "bg-green-900 text-green-300"
                    : statusData.status === "waiting_approval"
                      ? "bg-yellow-900 text-yellow-300"
                      : "bg-blue-900 text-blue-300"
                }`}
              >
                {statusData.status.toUpperCase()}
              </span>
            </div>

            {/* Processing */}
            {statusData.status === "processing" && (
              <div className="text-center py-8 animate-pulse text-slate-300">
                AI is thinking...
              </div>
            )}

            {/* Human intervention */}
            {statusData.status === "waiting_approval" && (
              <div className="space-y-4">
                <div className="bg-yellow-900/30 border border-yellow-700 p-3 rounded text-yellow-200 text-sm">
                  AI generated a draft. Review, edit if needed, and approve.
                </div>
                <textarea
                  className="w-full h-40 bg-slate-900 border border-slate-600 rounded p-4 text-slate-200 focus:ring-2 focus:ring-blue-500 outline-none"
                  value={editDraft}
                  onChange={(e) => setEditDraft(e.target.value)}
                />
                <button
                  onClick={approveTask}
                  disabled={loading}
                  className="w-full bg-green-600 hover:bg-green-500 py-3 rounded font-bold transition disabled:opacity-50"
                >
                  Approve &amp; Resume
                </button>
              </div>
            )}

            {/* Completed */}
            {statusData.status === "completed" && (
              <div className="space-y-4">
                <div className="bg-slate-900 p-4 rounded border border-slate-700">
                  <p className="text-slate-400 text-sm mb-2">Final output:</p>
                  <p className="whitespace-pre-wrap">
                    {statusData.current_draft}
                  </p>
                </div>
                <button
                  onClick={resetTask}
                  className="w-full bg-slate-700 hover:bg-slate-600 py-2 rounded text-sm transition"
                >
                  New Task
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
