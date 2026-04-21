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

export type PlanStatus = "mapped" | "clarification_needed" | "unsupported";

export interface PlanResultView {
  status: PlanStatus;
  originalText: string;
  capability: string | null;
  parameters: Record<string, unknown>;
  confidence: number;
  rationale: string;
  ambiguity: string | null;
  matchedRule: string | null;
}

export interface PlanActionView {
  actionId: string;
  capability: string;
  status: string;
  autoProposed: boolean;
}

export interface BrowserContextView {
  url: string | null;
  title: string | null;
  textExcerpt: string | null;
  byteCount: number;
  source: string | null;
  updatedAt: string | null;
}

export type WorkflowStatus =
  | "queued"
  | "in_progress"
  | "waiting_for_approval"
  | "blocked"
  | "completed"
  | "failed";

export type WorkflowStepStatus =
  | "pending"
  | "running"
  | "waiting_for_approval"
  | "completed"
  | "failed"
  | "blocked"
  | "skipped";

export interface WorkflowStepView {
  index: number;
  capability: string;
  parameters: Record<string, unknown>;
  intent: string;
  status: WorkflowStepStatus;
  actionId: string | null;
  resultSummary: string | null;
  error: string | null;
}

export interface WorkflowView {
  workflowId: string;
  taskId: string;
  objective: string;
  patternId: string;
  status: WorkflowStatus;
  currentStep: number;
  createdAt: string;
  updatedAt: string;
  error: string | null;
  steps: WorkflowStepView[];
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
  currentPlan?: PlanResultView | null;
  planAction?: PlanActionView | null;
  voice?: VoiceSnapshot;
  browserContext?: BrowserContextView | null;
  workflow?: WorkflowView | null;
}

export type StructuredCapability =
  | "browser.navigate"
  | "browser.read_page"
  | "browser.summarize"
  | "browser.current_page"
  | "filesystem.read"
  | "filesystem.list"
  | "filesystem.write"
  | "app.launch";

