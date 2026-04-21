import { FormEvent, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { ActionResultView, StructuredCapability } from "./contracts";

type FieldSpec = { key: string; label: string; placeholder: string; textarea?: boolean };

const CAPABILITY_FIELDS: Record<StructuredCapability, FieldSpec[]> = {
  "browser.navigate":  [{ key: "url",  label: "URL",  placeholder: "https://example.com" }],
  "browser.read_page": [{ key: "url",  label: "URL",  placeholder: "https://example.com" }],
  "filesystem.read":   [{ key: "path", label: "Path", placeholder: "configs/policy.default.json" }],
  "filesystem.list":   [{ key: "path", label: "Path", placeholder: "configs" }],
  "filesystem.write":  [
    { key: "path",    label: "Path (must be under runtime/sandbox/)", placeholder: "runtime/sandbox/hello.txt" },
    { key: "content", label: "Content", placeholder: "file contents", textarea: true },
  ],
  "app.launch":        [{ key: "name", label: "Allowlisted app name", placeholder: "notepad | calc | explorer | mspaint" }],
};

const CAPABILITIES: StructuredCapability[] = [
  "browser.navigate",
  "browser.read_page",
  "filesystem.read",
  "filesystem.list",
  "filesystem.write",
  "app.launch",
];

interface ProposeOutcome {
  status: string;
  decision?: { risk_tier: number; requires_approval: boolean; blocked: boolean; reason: string };
  approval?: { approval_id: string; capability: string };
  result?: { status: string; summary: string };
  action_id?: string;
}

interface Props {
  currentTaskId: string | null | undefined;
  degraded: boolean;
  onAfterAction: () => void;
  latestResult: ActionResultView | null | undefined;
}

export function ActionPanel({ currentTaskId, degraded, onAfterAction, latestResult }: Props) {
  const [capability, setCapability] = useState<StructuredCapability>("browser.read_page");
  const [values, setValues] = useState<Record<string, string>>({});
  const [dryRun, setDryRun] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastOutcome, setLastOutcome] = useState<ProposeOutcome | null>(null);

  const fields = CAPABILITY_FIELDS[capability];

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (pending || degraded) return;
    setPending(true);
    setError(null);
    try {
      const parameters: Record<string, string> = {};
      for (const f of fields) parameters[f.key] = values[f.key] ?? "";
      const outcome = await invoke<ProposeOutcome>("propose_action", {
        capability,
        parameters,
        taskId: currentTaskId ?? null,
        intent: `HUD action: ${capability}`,
        confidence: 0.95,
        dryRun,
      });
      setLastOutcome(outcome);
      onAfterAction();
    } catch (err) {
      setError(typeof err === "string" ? err : String(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="panel panel-actions">
      <div className="panel-head">
        <h2>Structured Action</h2>
        <span>Typed, policy-gated</span>
      </div>

      <form className="action-form" onSubmit={onSubmit}>
        <label>
          Capability
          <select
            value={capability}
            onChange={(e) => { setCapability(e.target.value as StructuredCapability); setValues({}); }}
            disabled={pending || degraded}
          >
            {CAPABILITIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>

        {fields.map((f) => (
          <label key={f.key}>
            {f.label}
            {f.textarea ? (
              <textarea
                rows={3}
                value={values[f.key] ?? ""}
                placeholder={f.placeholder}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                disabled={pending || degraded}
              />
            ) : (
              <input
                type="text"
                value={values[f.key] ?? ""}
                placeholder={f.placeholder}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                disabled={pending || degraded}
                autoComplete="off"
              />
            )}
          </label>
        ))}

        <label className="inline">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={pending || degraded}
          />
          Dry-run (preview only)
        </label>

        <div className="button-row">
          <button type="submit" disabled={pending || degraded}>
            {pending ? "Proposing…" : "Propose"}
          </button>
        </div>

        {error && <p className="form-error">{error}</p>}

        {lastOutcome && (
          <div className="outcome">
            <strong>Outcome:</strong>
            <span className={`outcome-status outcome-${lastOutcome.status}`}>{lastOutcome.status}</span>
            {lastOutcome.decision && (
              <small>Tier {lastOutcome.decision.risk_tier} · {lastOutcome.decision.reason}</small>
            )}
            {lastOutcome.result?.summary && <small>{lastOutcome.result.summary}</small>}
            {lastOutcome.approval && (
              <small>Pending approval: {lastOutcome.approval.approval_id}</small>
            )}
          </div>
        )}
      </form>

      {latestResult && (
        <div className="latest-result">
          <div className="panel-head">
            <h3>Latest Action Result</h3>
            <span className={`outcome-status outcome-${latestResult.status}`}>{latestResult.status}</span>
          </div>
          <p><code>{latestResult.capability}</code> — {latestResult.summary}</p>
          <details>
            <summary>Verification</summary>
            <pre>{JSON.stringify(latestResult.verification, null, 2)}</pre>
          </details>
          <details>
            <summary>Output</summary>
            <pre>{JSON.stringify(latestResult.output, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}
