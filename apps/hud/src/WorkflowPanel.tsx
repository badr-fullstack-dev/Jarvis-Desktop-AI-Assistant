import { WorkflowView, WorkflowStepStatus } from "./contracts";

interface Props {
  workflow: WorkflowView | null | undefined;
}

const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  in_progress: "In progress",
  waiting_for_approval: "Waiting for approval",
  blocked: "Blocked",
  completed: "Completed",
  failed: "Failed",
};

const STEP_LABEL: Record<WorkflowStepStatus, string> = {
  pending: "pending",
  running: "running",
  waiting_for_approval: "awaiting approval",
  completed: "done",
  failed: "failed",
  blocked: "blocked",
  skipped: "skipped",
};

function stepClass(status: WorkflowStepStatus, isCurrent: boolean): string {
  return `workflow-step workflow-step-${status}${isCurrent ? " workflow-step-current" : ""}`;
}

export function WorkflowPanel({ workflow }: Props) {
  return (
    <section className="panel panel-workflow">
      <div className="panel-head">
        <h2>Workflow</h2>
        <span>{workflow ? STATUS_LABEL[workflow.status] ?? workflow.status : "No workflow active"}</span>
      </div>

      {!workflow && (
        <p className="empty-hint">
          Bounded multi-step requests appear here. Examples:
          &ldquo;open https://example.com and read it&rdquo;,
          &ldquo;read https://example.com then summarize this page&rdquo;,
          &ldquo;write hi to runtime/sandbox/x.txt then read it back&rdquo;.
        </p>
      )}

      {workflow && (
        <>
          <dl className="workflow-meta">
            <div>
              <dt>Pattern</dt>
              <dd>{workflow.patternId}</dd>
            </div>
            <div>
              <dt>Step</dt>
              <dd>
                {Math.min(workflow.currentStep + 1, workflow.steps.length)} of {workflow.steps.length}
              </dd>
            </div>
            <div>
              <dt>Objective</dt>
              <dd className="workflow-objective">{workflow.objective}</dd>
            </div>
          </dl>

          {workflow.error && (
            <p className="form-error">{workflow.error}</p>
          )}

          <ol className="workflow-steps">
            {workflow.steps.map((step) => {
              const isCurrent = step.index === workflow.currentStep
                && workflow.status !== "completed"
                && workflow.status !== "failed";
              return (
                <li key={step.index} className={stepClass(step.status, isCurrent)}>
                  <div className="workflow-step-head">
                    <strong>Step {step.index + 1}</strong>
                    <span className="workflow-step-cap">{step.capability}</span>
                    <span className="workflow-step-status">{STEP_LABEL[step.status]}</span>
                  </div>
                  <p className="workflow-step-intent">{step.intent}</p>
                  {step.resultSummary && (
                    <p className="workflow-step-result">{step.resultSummary}</p>
                  )}
                  {step.error && (
                    <p className="workflow-step-error">{step.error}</p>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </section>
  );
}
