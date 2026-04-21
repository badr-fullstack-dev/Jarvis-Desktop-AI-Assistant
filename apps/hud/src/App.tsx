import { demoState } from "./demoState";

function statusClass(status: string): string {
  return `agent agent-${status}`;
}

export default function App() {
  const state = demoState;

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
          <small>Security and inspectability over hidden automation</small>
        </div>
      </header>

      <main className="layout">
        <section className="panel panel-task">
          <h2>Live Task</h2>
          <p>{state.task}</p>
          <div className="transcript">
            <span>Voice Transcript</span>
            <strong>{state.transcript}</strong>
          </div>
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
                <button>Review</button>
                <button className="secondary">Deny</button>
              </div>
            </article>
          ))}
        </section>

        <section className="panel panel-memory">
          <div className="panel-head">
            <h2>Memory Candidates</h2>
            <span>Curated learning only</span>
          </div>
          <div className="memory-list">
            {state.memory.map((entry) => (
              <article key={entry.memoryId} className="memory-card">
                <div className="memory-meta">
                  <span>{entry.kind}</span>
                  <span>{Math.round(entry.trustScore * 100)}% trust</span>
                </div>
                <p>{entry.summary}</p>
                <strong>{entry.status}</strong>
              </article>
            ))}
          </div>
        </section>

        <section className="panel panel-trace">
          <div className="panel-head">
            <h2>Trace Replay</h2>
            <span>Signed append-only events</span>
          </div>
          <ol className="trace-list">
            {state.trace.map((item) => (
              <li key={`${item.time}-${item.type}`}>
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

