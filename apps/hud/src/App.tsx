import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { HudState } from "./contracts";
import { ActionPanel } from "./ActionPanel";
import { BrowserPanel } from "./BrowserPanel";
import { DesktopPanel } from "./DesktopPanel";
import { MemoryPanel } from "./MemoryPanel";
import { PlanPanel } from "./PlanPanel";
import { VoicePanel } from "./VoicePanel";
import { WorkflowPanel } from "./WorkflowPanel";

type LiveHudState = HudState & { degraded?: boolean; degradedReason?: string };

const EMPTY_STATE: LiveHudState = {
  mode: "Guarded Autonomy",
  task: "",
  transcript: "",
  agents: [
    { id: "planner", label: "Planner", role: "Task decomposition", status: "idle",
      detail: "Waiting for a task." },
    { id: "researcher", label: "Researcher", role: "Evidence collection", status: "idle",
      detail: "Waiting for a task." },
    { id: "security", label: "Security Sentinel", role: "Risk scoring", status: "idle",
      detail: "Waiting for a task." },
    { id: "verifier", label: "Verifier", role: "Outcome validation", status: "idle",
      detail: "Waiting for a task." },
  ],
  approvals: [],
  memory: [],
  trace: [],
};

const POLL_MS = 3000;

function statusClass(status: string): string {
  return `agent agent-${status}`;
}

export default function App() {
  const [state, setState] = useState<LiveHudState>(EMPTY_STATE);
  const [bridgeError, setBridgeError] = useState<string | null>(null);
  const [objective, setObjective] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [approvalBusy, setApprovalBusy] = useState<string | null>(null);
  const [memoryBusy, setMemoryBusy] = useState<string | null>(null);
  const [memoryToast, setMemoryToast] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [ttsEnabled, setTtsEnabled] = useState<boolean>(() => {
    try { return localStorage.getItem("jarvis.tts") !== "off"; } catch { return true; }
  });
  const pollingRef = useRef<number | null>(null);
  const prevApprovalsRef = useRef<Set<string>>(new Set());
  const prevResultRef = useRef<string | null>(null);
  const prevDeniedRef = useRef<number>(0);

  const refresh = useCallback(async () => {
    try {
      const next = await invoke<LiveHudState>("get_hud_state");
      setState(next);
      setBridgeError(null);
    } catch (err) {
      setBridgeError(typeof err === "string" ? err : String(err));
    }
  }, []);

  useEffect(() => {
    refresh();
    pollingRef.current = window.setInterval(refresh, POLL_MS);
    return () => {
      if (pollingRef.current !== null) window.clearInterval(pollingRef.current);
    };
  }, [refresh]);

  useEffect(() => {
    try { localStorage.setItem("jarvis.tts", ttsEnabled ? "on" : "off"); } catch { /* ignore */ }
  }, [ttsEnabled]);

  const firstTick = useRef(true);
  useEffect(() => {
    const speak = (text: string) => {
      if (!ttsEnabled) return;
      if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
      try {
        const u = new SpeechSynthesisUtterance(text);
        u.rate = 1.05;
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(u);
      } catch { /* ignore */ }
    };

    const currentApprovals = new Set(state.approvals.map((a) => a.approvalId));
    const currentResultKey = state.latestResult
      ? `${state.latestResult.actionId}:${state.latestResult.status}`
      : null;
    const deniedCount = state.trace.filter((t) => t.type === "approval.denied").length;

    if (!firstTick.current) {
      for (const id of currentApprovals) {
        if (!prevApprovalsRef.current.has(id)) {
          speak("Approval required.");
          break;
        }
      }
      if (currentResultKey && currentResultKey !== prevResultRef.current) {
        if (state.latestResult?.status === "executed") speak("Action completed.");
        else if (state.latestResult?.status === "blocked") speak("Action blocked.");
        else if (state.latestResult?.status === "failed") speak("Action failed.");
      }
      if (deniedCount > prevDeniedRef.current) {
        speak("Approval denied.");
      }
    }

    prevApprovalsRef.current = currentApprovals;
    prevResultRef.current = currentResultKey;
    prevDeniedRef.current = deniedCount;
    firstTick.current = false;
  }, [state.approvals, state.latestResult, state.trace, ttsEnabled]);

  const onApprove = async (approvalId: string) => {
    if (approvalBusy) return;
    setApprovalBusy(approvalId);
    setApprovalError(null);
    try {
      await invoke("execute_action", { approvalId });
      await refresh();
    } catch (err) {
      setApprovalError(typeof err === "string" ? err : String(err));
    } finally {
      setApprovalBusy(null);
    }
  };

  const onDeny = async (approvalId: string) => {
    if (approvalBusy) return;
    const reason = window.prompt("Reason for denial (optional):", "") ?? "";
    setApprovalBusy(approvalId);
    setApprovalError(null);
    try {
      await invoke("deny_action", { approvalId, reason });
      await refresh();
    } catch (err) {
      setApprovalError(typeof err === "string" ? err : String(err));
    } finally {
      setApprovalBusy(null);
    }
  };

  const onMemoryApprove = useCallback(async (memoryId: string) => {
    if (memoryBusy) return;
    setMemoryBusy(memoryId);
    try {
      await invoke("memory_approve", { memoryId });
      setMemoryToast("Memory approved.");
      await refresh();
    } catch (err) {
      setMemoryToast(`Approve failed: ${typeof err === "string" ? err : String(err)}`);
    } finally {
      setMemoryBusy(null);
    }
  }, [memoryBusy, refresh]);

  const onMemoryReject = useCallback(async (memoryId: string, reason: string) => {
    if (memoryBusy) return;
    setMemoryBusy(memoryId);
    try {
      await invoke("memory_reject", { memoryId, reason });
      setMemoryToast("Memory rejected.");
      await refresh();
    } catch (err) {
      setMemoryToast(`Reject failed: ${typeof err === "string" ? err : String(err)}`);
    } finally {
      setMemoryBusy(null);
    }
  }, [memoryBusy, refresh]);

  const onMemoryExpire = useCallback(async (memoryId: string) => {
    if (memoryBusy) return;
    setMemoryBusy(memoryId);
    try {
      await invoke("memory_expire", { memoryId });
      setMemoryToast("Memory expired.");
      await refresh();
    } catch (err) {
      setMemoryToast(`Expire failed: ${typeof err === "string" ? err : String(err)}`);
    } finally {
      setMemoryBusy(null);
    }
  }, [memoryBusy, refresh]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const text = objective.trim();
    if (!text || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await invoke("submit_task", { objective: text });
      setObjective("");
      await refresh();
      if (ttsEnabled && "speechSynthesis" in window) {
        try { window.speechSynthesis.speak(new SpeechSynthesisUtterance("Task accepted.")); } catch { /* ignore */ }
      }
    } catch (err) {
      setSubmitError(typeof err === "string" ? err : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const degraded = bridgeError !== null || state.degraded === true;
  const degradedMessage = bridgeError ?? state.degradedReason ?? "Python runtime unavailable.";

  return (
    <div className="shell">
      <div className="bg-grid" />
      <header className="hero">
        <div>
          <p className="eyebrow">Windows-First Guarded Assistant</p>
          <h1>Jarvis Command HUD</h1>
          <p className="lede">
            A cinematic control surface for plans, approvals, memory, verification,
            and subagent coordination.
          </p>
        </div>
        <div className="mode-card">
          <span>Mode</span>
          <strong>{state.mode}</strong>
          <small>
            {degraded
              ? "Degraded: showing last known state"
              : "Live runtime — signed audit active"}
          </small>
        </div>
      </header>

      {degraded && (
        <div className="degraded-banner" role="status">
          <strong>Bridge offline.</strong>
          <span>{degradedMessage}</span>
          <small>Start it with: <code>python -m jarvis_core</code></small>
        </div>
      )}

      <main className="layout">
        <section className="panel panel-task">
          <h2>Live Task</h2>
          <p>{state.task || "No active task. Submit one below."}</p>
          <div className="transcript">
            <span>Last Input</span>
            <strong>{state.transcript || "—"}</strong>
          </div>

          <form className="task-form" onSubmit={onSubmit}>
            <label htmlFor="task-objective">Submit a text task</label>
            <div className="task-form-row">
              <input
                id="task-objective"
                type="text"
                placeholder="e.g. Review the installer page and wait for approval…"
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                disabled={submitting || degraded}
                autoComplete="off"
              />
              <button type="submit" disabled={submitting || degraded || !objective.trim()}>
                {submitting ? "Submitting…" : "Submit"}
              </button>
            </div>
            {submitError && <p className="form-error">{submitError}</p>}
          </form>
        </section>

        <section className="panel panel-agents">
          <div className="panel-head">
            <h2>Subagents</h2>
            <span>{state.agents.length} active lanes</span>
          </div>
          <div className="agent-grid">
            {state.agents.map((agent) => (
              <article key={agent.id} className={statusClass(agent.status)}>
                <div className="agent-title">
                  <h3>{agent.label}</h3>
                  <span>{agent.status}</span>
                </div>
                <p className="agent-role">{agent.role}</p>
                <p>{agent.detail}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="panel panel-approvals">
          <div className="panel-head">
            <h2>Approval Center</h2>
            <span>{state.approvals.length} pending</span>
          </div>
          {state.approvals.length === 0 && (
            <p className="empty-hint">No approvals pending.</p>
          )}
          {state.approvals.map((approval) => (
            <article key={approval.approvalId} className="approval-card">
              <div>
                <p className="eyebrow">Tier {approval.tier} Action</p>
                <h3>{approval.title}</h3>
              </div>
              <p>{approval.reason}</p>
              <dl>
                <div>
                  <dt>Capability</dt>
                  <dd>{approval.capability}</dd>
                </div>
                <div>
                  <dt>Target</dt>
                  <dd>{approval.target}</dd>
                </div>
              </dl>
              <div className="button-row">
                <button
                  type="button"
                  disabled={approvalBusy !== null || degraded}
                  onClick={() => onApprove(approval.approvalId)}
                >
                  {approvalBusy === approval.approvalId ? "Approving…" : "Approve & Execute"}
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={approvalBusy !== null || degraded}
                  onClick={() => onDeny(approval.approvalId)}
                >
                  Deny
                </button>
              </div>
            </article>
          ))}
          {approvalError && <p className="form-error">{approvalError}</p>}
        </section>

        <PlanPanel plan={state.currentPlan} planAction={state.planAction} />

        <WorkflowPanel workflow={state.workflow} />

        <BrowserPanel
          context={state.browserContext}
          degraded={degraded}
          onAfterAction={refresh}
        />

        <DesktopPanel desktop={state.desktop ?? null} />

        <VoicePanel
          voice={state.voice}
          degraded={degraded}
          onAfterAction={refresh}
          ttsEnabled={ttsEnabled}
          onToggleTts={setTtsEnabled}
        />

        <ActionPanel
          currentTaskId={state.currentTaskId}
          degraded={degraded}
          onAfterAction={refresh}
          latestResult={state.latestResult ?? null}
        />

        <MemoryPanel
          pending={state.memory.filter((m) => m.status === "candidate")}
          approved={state.memory.filter((m) => m.status === "approved")}
          recent={state.memory
            .filter((m) => m.status === "rejected" || m.status === "expired")
            .slice(-10)}
          onApprove={onMemoryApprove}
          onReject={onMemoryReject}
          onExpire={onMemoryExpire}
          busyId={memoryBusy}
          lastAction={memoryToast}
        />

        <section className="panel panel-trace">
          <div className="panel-head">
            <h2>Trace Replay</h2>
            <span>Signed append-only events</span>
          </div>
          {state.trace.length === 0 && (
            <p className="empty-hint">Trace is empty until a task runs.</p>
          )}
          <ol className="trace-list">
            {state.trace.map((item, idx) => (
              <li key={`${idx}-${item.type}`}>
                <span>{item.time}</span>
                <div>
                  <strong>{item.type}</strong>
                  <p>{item.summary}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      </main>
    </div>
  );
}
