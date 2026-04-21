export type AgentStatus = "idle" | "running" | "blocked" | "done";

export interface AgentCard {
  id: string;
  label: string;
  role: string;
  status: AgentStatus;
  detail: string;
}

export interface ApprovalCard {
  approvalId: string;
  title: string;
  tier: number;
  capability: string;
  reason: string;
  target: string;
}

export interface MemoryCandidate {
  memoryId: string;
  kind: "profile" | "operational" | "lesson" | "tool";
  summary: string;
  trustScore: number;
  status: "candidate" | "approved" | "rejected" | "expired";
}

export interface TraceItem {
  time: string;
  type: string;
  summary: string;
}

export interface HudState {
  mode: string;
  task: string;
  transcript: string;
  agents: AgentCard[];
  approvals: ApprovalCard[];
  memory: MemoryCandidate[];
  trace: TraceItem[];
}

