import { PlanActionView, PlanResultView } from "./contracts";

interface Props {
  plan: PlanResultView | null | undefined;
  planAction: PlanActionView | null | undefined;
}

function renderParams(params: Record<string, unknown>): string {
  const entries = Object.entries(params ?? {});
  if (entries.length === 0) return "—";
  return entries.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ");
}

export function PlanPanel({ plan, planAction }: Props) {
  if (!plan) {
    return (
      <section className="panel panel-plan">
        <div className="panel-head">
          <h2>Auto-Plan</h2>
          <span>Deterministic interpreter</span>
        </div>
        <p className="empty-hint">
          Submit a text or voice task to see how the planner maps it to a
          structured action.
        </p>
      </section>
    );
  }

  const status = plan.status;
  const badgeClass =
    status === "mapped"
      ? "plan-badge plan-mapped"
      : status === "clarification_needed"
      ? "plan-badge plan-clarify"
      : "plan-badge plan-unsupported";

  const humanStatus =
    status === "mapped"
      ? "Mapped"
      : status === "clarification_needed"
      ? "Clarification needed"
      : "Unsupported";

  return (
    <section className="panel panel-plan">
      <div className="panel-head">
        <h2>Auto-Plan</h2>
        <span className={badgeClass}>{humanStatus}</span>
      </div>

      <p className="voice-hint">
        Original request: <code>{plan.originalText || "—"}</code>
      </p>

      {status === "mapped" && (
        <dl className="plan-details">
          <div>
            <dt>Capability</dt>
            <dd><code>{plan.capability}</code></dd>
          </div>
          <div>
            <dt>Parameters</dt>
            <dd><code>{renderParams(plan.parameters)}</code></dd>
          </div>
          <div>
            <dt>Confidence</dt>
            <dd>{Math.round(plan.confidence * 100)}%</dd>
          </div>
          <div>
            <dt>Rule</dt>
            <dd><code>{plan.matchedRule || "—"}</code></dd>
          </div>
          <div>
            <dt>Why</dt>
            <dd>{plan.rationale || "—"}</dd>
          </div>
          {planAction && (
            <div>
              <dt>Auto-proposed</dt>
              <dd>
                status=<code>{planAction.status}</code>, action=
                <code>{planAction.actionId}</code>
              </dd>
            </div>
          )}
        </dl>
      )}

      {status !== "mapped" && (
        <>
          <p className="form-error" role="status">
            {plan.ambiguity || "The planner declined to map this request."}
          </p>
          <p className="voice-hint">
            Supported v1 requests:
            <ul>
              <li><code>open https://example.com</code> / <code>go to example.com</code></li>
              <li><code>read https://example.com</code> / <code>read configs/policy.default.json</code></li>
              <li><code>list files in configs</code> / <code>ls runtime/sandbox</code></li>
              <li><code>write hello to runtime/sandbox/hello.txt</code></li>
              <li><code>open notepad</code> / <code>launch calculator</code></li>
            </ul>
            Anything else is intentionally not auto-mapped — use the
            Structured Action panel to submit it manually if needed.
          </p>
        </>
      )}
    </section>
  );
}
