import { HudState } from "./contracts";

export const demoState: HudState = {
  mode: "Guarded Autonomy",
  task: "Prepare a safe plan to install a package, summarize the vendor page, and wait for approval before making changes.",
  transcript: "Jarvis, review the installer page, explain the risks, and only continue if I approve the install.",
  agents: [
    {
      id: "planner",
      label: "Planner",
      role: "Task decomposition",
      status: "running",
      detail: "Splitting the request into research, verification, and approval checkpoints."
    },
    {
      id: "researcher",
      label: "Researcher",
      role: "Evidence collection",
      status: "running",
      detail: "Collecting vendor details, signatures, and installation notes."
    },
    {
      id: "security",
      label: "Security Sentinel",
      role: "Risk scoring",
      status: "blocked",
      detail: "Install action scored Tier 2 and requires approval."
    },
    {
      id: "verifier",
      label: "Verifier",
      role: "Outcome validation",
      status: "idle",
      detail: "Waiting for execution evidence before postflight checks."
    }
  ],
  approvals: [
    {
      approvalId: "approval-001",
      title: "Install Browser Extension",
      tier: 2,
      capability: "app.install",
      reason: "The request modifies the local machine and may persist beyond the current task.",
      target: "Vendor-signed installer from trusted source"
    }
  ],
  memory: [
    {
      memoryId: "lesson-001",
      kind: "lesson",
      summary: "For installs, gather vendor signature details before asking for approval.",
      trustScore: 0.84,
      status: "candidate"
    },
    {
      memoryId: "profile-001",
      kind: "profile",
      summary: "User prefers spoken summaries before any Tier 2 action.",
      trustScore: 0.92,
      status: "approved"
    }
  ],
  trace: [
    {
      time: "09:14:21",
      type: "task.created",
      summary: "Voice request ingested and opened as task session."
    },
    {
      time: "09:14:24",
      type: "subagent.started",
      summary: "Planner, Researcher, and Security Sentinel started in parallel."
    },
    {
      time: "09:14:31",
      type: "approval.requested",
      summary: "Install action paused pending explicit approval."
    }
  ]
};

