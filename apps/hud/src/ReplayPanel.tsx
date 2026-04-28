import {
  EventLogHealth,
  ReliabilityCounters,
  ReplayTimeline,
  TaskSummaryView,
} from "./contracts";

interface Props {
  recent: TaskSummaryView[];
  selectedTaskId: string | null;
  selectedReplay: ReplayTimeline | null;
  counters: ReliabilityCounters | null;
  health: EventLogHealth | null;
  onSelectTask: (taskId: string) => void;
}

function HealthBadge({ health }: { health: EventLogHealth | null }) {
  // Per a11y review: the tamper string is a security signal, so it gets
  // role="alert" (assertive). The benign "ok" path uses aria-live="polite"
  // and shares no element with the alert path.
  if (!health) {
    return <span aria-live="polite">log unknown</span>;
  }
  if (!health.ok) {
    return (
      <span className="replay-health-bad" role="alert">
        EVENT-LOG TAMPER DETECTED
      </span>
    );
  }
  return (
    <span className="replay-health-ok" aria-live="polite">
      event-log ok · {health.recordCount} events
    </span>
  );
}

export function ReplayPanel({
  recent,
  selectedTaskId,
  selectedReplay,
  counters,
  health,
  onSelectTask,
}: Props) {
  return (
    <section className="panel panel-replay" aria-labelledby="replay-panel-heading">
      <div className="panel-head">
        <h2 id="replay-panel-heading">Replay & Reliability</h2>
        <HealthBadge health={health} />
      </div>
      {/*
        Per a11y review: render the polite live region unconditionally
        with empty text when healthy; toggling textContent on a stable
        node keeps assistive tech from racing the assertive `role="alert"`
        tamper line above. The note is a STATE not an event so polite is
        correct.
      */}
      <p
        className={`replay-history-untrusted${
          health && !health.ok ? "" : " sr-only"
        }`}
        aria-live="polite"
      >
        {health && !health.ok
          ? "Restored history is not trusted while audit-log verification fails."
          : ""}
      </p>

      <h3 className="replay-section-heading">Recent tasks</h3>
      {recent.length === 0 ? (
        <p className="empty-hint">No tasks yet.</p>
      ) : (
        <ul className="replay-task-list">
          {recent.map((t) => {
            const selected = t.taskId === selectedTaskId;
            return (
              <li key={t.taskId}>
                <button
                  type="button"
                  className={`replay-task-button${selected ? " replay-task-selected" : ""}`}
                  // aria-current is the right semantic for "current item
                  // in a set" (which task's replay is shown). aria-pressed
                  // would imply this is a toggle.
                  aria-current={selected ? "true" : undefined}
                  onClick={() => onSelectTask(t.taskId)}
                >
                  <span className="replay-task-objective">
                    {t.objective || "(no objective)"}
                  </span>
                  <span className="replay-task-meta">
                    {t.status} · {t.actionCount}{" "}
                    action{t.actionCount !== 1 ? "s" : ""}
                    {t.failureCount > 0
                      ? ` · ${t.failureCount} failure${t.failureCount !== 1 ? "s" : ""}`
                      : ""}
                    {t.pendingApprovals > 0
                      ? ` · ${t.pendingApprovals} pending`
                      : ""}
                  </span>
                  {/*
                    Restart-safety badges (Checkpoint 11). Plain text
                    inside the button so the accessible name reads
                    "objective. status … restored." in one stop. Kept
                    as words (never color-only) and styled with both a
                    visible border AND text contrast that exceeds 4.5:1
                    against the panel background.
                  */}
                  {t.origin === "history" && (
                    <span className="replay-task-badge replay-badge-restored">
                      restored
                    </span>
                  )}
                  {t.interrupted && (
                    <span className="replay-task-badge replay-badge-interrupted">
                      interrupted
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <h3 className="replay-section-heading">Replay timeline</h3>
      {!selectedReplay ? (
        <p className="empty-hint">Select a task above to view its replay.</p>
      ) : selectedReplay.events.length === 0 ? (
        <p className="empty-hint">Task has no recorded events yet.</p>
      ) : (
        <ol className="replay-timeline">
          {selectedReplay.events.map((e) => (
            <li
              key={e.index}
              className={`replay-event replay-event-${e.status ?? "info"}`}
            >
              <div className="replay-event-row">
                <span className="replay-event-time">{e.timestamp}</span>
                <span className="replay-event-type">{e.type}</span>
                {e.capability && (
                  <span className="replay-event-cap">
                    <code>{e.capability}</code>
                  </span>
                )}
                {e.status && (
                  <span className={`replay-event-status replay-status-${e.status}`}>
                    {e.status}
                  </span>
                )}
                {e.verificationOk !== null && (
                  <span className="replay-event-verify">
                    verify: {e.verificationOk ? "ok" : "fail"}
                  </span>
                )}
              </div>
              <div className="replay-event-summary">{e.summary}</div>
              {e.errorType && (
                <div className="replay-event-error">
                  error_type: <code>{e.errorType}</code>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}

      <h3 className="replay-section-heading">Capability reliability</h3>
      {!counters || Object.keys(counters.byCapability).length === 0 ? (
        <p className="empty-hint">No actions recorded yet.</p>
      ) : (
        <table className="replay-counter-table">
          <caption className="sr-only">
            Action counts by capability and status
          </caption>
          <thead>
            <tr>
              <th scope="col">Capability</th>
              <th scope="col">Executed</th>
              <th scope="col">Failed</th>
              <th scope="col">Blocked</th>
              <th scope="col">Awaiting</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(counters.byCapability)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([cap, c]) => (
                <tr key={cap}>
                  <th scope="row">
                    <code>{cap}</code>
                  </th>
                  <td>{c.executed}</td>
                  <td className={c.failed > 0 ? "replay-cell-bad" : ""}>{c.failed}</td>
                  <td className={c.blocked > 0 ? "replay-cell-bad" : ""}>{c.blocked}</td>
                  <td>{c.awaiting}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}

      {counters && counters.totals.tasks > 0 && (
        <p className="replay-totals">
          {counters.totals.tasks} task{counters.totals.tasks !== 1 ? "s" : ""}{" "}
          · {counters.totals.actions} action{counters.totals.actions !== 1 ? "s" : ""}{" "}
          · {counters.totals.failures} failure{counters.totals.failures !== 1 ? "s" : ""}{" "}
          · {counters.totals.approvals} approval{counters.totals.approvals !== 1 ? "s" : ""}{" "}
          · {counters.totals.denials} denial{counters.totals.denials !== 1 ? "s" : ""}
        </p>
      )}
    </section>
  );
}
