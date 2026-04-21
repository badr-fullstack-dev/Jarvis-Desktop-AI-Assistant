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

export interface ActionResultView {
  actionId: string;
  capability: string;
  status: string;
  summary: string;
  output: Record<string, unknown>;
  verification: Record<string, unknown>;
}

export type VoiceSessionState = "idle" | "recording" | "transcribing" | "ready" | "error";

export interface VoiceSnapshot {
  state: VoiceSessionState;
  enabled: boolean;
  transcript: string | null;
  error: string | null;
  provider: string;
  lastAudioBytes: number;
  lastMime: string | null;
  updatedAt: string;
}

export interface HudState {
  mode: string;
  task: string;
  transcript: string;
  agents: AgentCard[];
  approvals: ApprovalCard[];
  memory: MemoryCandidate[];
  trace: TraceItem[];
  latestResult?: ActionResultView | null;
  currentTaskId?: string | null;
  voice?: VoiceSnapshot;
}

export type StructuredCapability =
  | "browser.navigate"
  | "browser.read_page"
  | "filesystem.read"
  | "filesystem.list"
  | "filesystem.write"
  | "app.launch";

