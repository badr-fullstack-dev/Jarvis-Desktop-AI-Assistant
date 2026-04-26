import { MemoryCandidate } from "./contracts";

interface Props {
  pending: MemoryCandidate[];
  approved: MemoryCandidate[];
  recent: MemoryCandidate[];
  onApprove: (id: string) => Promise<void>;
  onReject: (id: string, reason: string) => Promise<void>;
  onExpire: (id: string) => Promise<void>;
  busyId: string | null;
  lastAction: string | null;
}

function MemoryCardHead({ m }: { m: MemoryCandidate }) {
  return (
    <div className="memory-card-head">
      <span className={`memory-kind memory-kind-${m.kind}`}>{m.kind}</span>
      <span className="memory-trust">trust {m.trustScore.toFixed(2)}</span>
    </div>
  );
}

function ariaBusyAttrs(busy: boolean) {
  // Per a11y review: prefer aria-disabled + click guard so focus is preserved
  // through async transitions. The handler itself bails when busy.
  return {
    "aria-disabled": busy ? true : undefined,
    "aria-busy": busy ? true : undefined,
  } as const;
}

export function MemoryPanel({
  pending, approved, recent,
  onApprove, onReject, onExpire,
  busyId, lastAction,
}: Props) {
  const pendingCount = pending.length;
  const approvedCount = approved.length;

  return (
    <section className="panel panel-memory" aria-labelledby="memory-panel-heading">
      <div className="panel-head">
        <h2 id="memory-panel-heading">Memory</h2>
        <span>
          {pendingCount} pending · {approvedCount} approved
        </span>
      </div>

      {/* Toast-style live region — announces approve/reject/expire results to AT.
          Empty when no recent action; aria-live polite avoids interrupting the user. */}
      <div className="memory-toast" role="status" aria-live="polite" aria-atomic="true">
        {lastAction ?? ""}
      </div>

      <h3 className="memory-group-heading">Pending proposals</h3>
      {pendingCount === 0 ? (
        <p className="empty-hint">No pending proposals.</p>
      ) : (
        <ul className="memory-list">
          {pending.map((m) => {
            const busy = busyId === m.memoryId;
            return (
              <li key={m.memoryId} className="memory-card">
                <MemoryCardHead m={m} />
                <p className="memory-summary">{m.summary}</p>
                {m.evidence && m.evidence.length > 0 && (
                  <details className="memory-evidence">
                    <summary>Evidence ({m.evidence.length})</summary>
                    <ul>
                      {m.evidence.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  </details>
                )}
                <div className="memory-actions">
                  <button
                    type="button"
                    onClick={() => { if (!busy) void onApprove(m.memoryId); }}
                    {...ariaBusyAttrs(busy)}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (busy) return;
                      const reason = window.prompt("Rejection reason (optional):") ?? "";
                      void onReject(m.memoryId, reason);
                    }}
                    {...ariaBusyAttrs(busy)}
                  >
                    Reject
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <h3 className="memory-group-heading">Approved</h3>
      {approvedCount === 0 ? (
        <p className="empty-hint">No approved memories yet.</p>
      ) : (
        <ul className="memory-list">
          {approved.map((m) => {
            const busy = busyId === m.memoryId;
            return (
              <li key={m.memoryId} className="memory-card memory-card-approved">
                <MemoryCardHead m={m} />
                <p className="memory-summary">{m.summary}</p>
                <div className="memory-actions">
                  <button
                    type="button"
                    onClick={() => { if (!busy) void onExpire(m.memoryId); }}
                    {...ariaBusyAttrs(busy)}
                  >
                    Expire
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {recent.length > 0 && (
        <details className="memory-recent">
          <summary>Recent ({recent.length})</summary>
          <ul className="memory-list">
            {recent.map((m) => (
              <li key={m.memoryId} className="memory-card memory-card-archived">
                <MemoryCardHead m={m} />
                <p className="memory-summary">{m.summary}</p>
                <p className="memory-status-line">
                  <span className="memory-status">{m.status}</span>
                  {m.reviewReason ? (
                    <span className="memory-reason"> — {m.reviewReason}</span>
                  ) : null}
                </p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
