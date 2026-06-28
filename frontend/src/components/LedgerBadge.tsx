import { useEffect, useState } from "react";
import { api, isAbort } from "../api";
import type { LedgerVerdict } from "../types";

// Shows the tamper-evidence verdict for a task's approval hash chain.
export function LedgerBadge({ taskId }: { taskId: number }) {
  const [verdict, setVerdict] = useState<LedgerVerdict | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setVerdict(null);
    setError(null);
    api
      .verifyLedger(taskId, controller.signal)
      .then(setVerdict)
      .catch((e: unknown) => {
        if (!isAbort(e)) setError(e instanceof Error ? e.message : String(e));
      });
    return () => controller.abort();
  }, [taskId]);

  if (error) return <span className="badge badge-warn">ledger: {error}</span>;
  if (!verdict) return <span className="badge">ledger: …</span>;

  const cls = verdict.valid ? "badge badge-ok" : "badge badge-bad";
  const label = verdict.valid
    ? `ledger verified · ${verdict.count} entr${verdict.count === 1 ? "y" : "ies"}`
    : `ledger TAMPERED · broken at #${verdict.broken_at}`;
  return (
    <span className={cls} title={`legacy (pre-hash-chain) rows: ${verdict.legacy}`}>
      {label}
    </span>
  );
}
