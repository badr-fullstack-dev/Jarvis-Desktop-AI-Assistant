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

export type MemoryKind = "profile" | "operational" | "lesson" | "tool";
export type MemoryStatus = "candidate" | "approved" | "rejected" | "expired";

export interface MemoryCandidate {
  memoryId: string;
  kind: MemoryKind;
  summary: string;
  trustScore: number;
  status: MemoryStatus;
  evidence?: string[];
  reviewedAt?: string | null;
  reviewedBy?: string | null;
  reviewReason?: string | null;
}

export interface MemoryHint {
  memoryId: string;
  kind: MemoryKind;
  summary: string;
  trustScore: number;
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
  memoryHints?: MemoryHint[];
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

export interface DesktopClipboardView {
  capability: "desktop.clipboard_read";
  status: string;
  summary: string;
  text: string | null;
  truncated: boolean;
  byteCount: number;
  updatedAt: string;
}

export interface DesktopClipboardWriteView {
  capability: "desktop.clipboard_write";
  status: string;
  summary: string;
  byteCount: number;
  updatedAt: string;
}

export interface DesktopNotificationView {
  capability: "desktop.notify";
  status: string;
  summary: string;
  title: string | null;
  message: string | null;
  channel: string | null;
  updatedAt: string;
}

export interface DesktopWindowInfo {
  hwnd: number;
  title: string;
  pid: number;
  exe: string | null;
}

export interface DesktopForegroundView {
  capability: "desktop.foreground_window";
  status: string;
  summary: string;
  window: DesktopWindowInfo | null;
  updatedAt: string;
}

export interface DesktopFocusView {
  capability: "app.focus";
  status: string;
  summary: string;
  name: string | null;
  focused: boolean;
  hwnd: number | null;
  pid: number | null;
  error: string | null;
  updatedAt: string;
}

export interface DesktopScreenshotView {
  capability: "desktop.screenshot_foreground" | "desktop.screenshot_full";
  status: string;
  summary: string;
  mode: "foreground" | "full";
  name: string | null;
  path: string | null;
  width: number;
  height: number;
  byteCount: number;
  updatedAt: string;
}

export interface DesktopOcrLine {
  text: string;
  confidence: number | null;
}

export interface DesktopOcrView {
  capability:
    | "desktop.ocr_foreground"
    | "desktop.ocr_full"
    | "desktop.ocr_screenshot";
  status: string;
  summary: string;
  mode: "foreground" | "full" | "screenshot";
  text: string;
  truncated: boolean;
  byteCount: number;
  charCount: number;
  lineCount: number;
  lines: DesktopOcrLine[];
  averageConfidence: number | null;
  language: string | null;
  provider: string;
  screenshotName: string | null;
  screenshotPath: string | null;
  screenshotWidth: number;
  screenshotHeight: number;
  screenshotBytes: number;
  updatedAt: string;
}

export interface DesktopView {
  clipboard: DesktopClipboardView | null;
  clipboardWrite: DesktopClipboardWriteView | null;
  notification: DesktopNotificationView | null;
  foregroundWindow: DesktopForegroundView | null;
  focus: DesktopFocusView | null;
  screenshotForeground: DesktopScreenshotView | null;
  screenshotFull: DesktopScreenshotView | null;
  latestScreenshot: DesktopScreenshotView | null;
  ocrForeground: DesktopOcrView | null;
  ocrFull: DesktopOcrView | null;
  ocrScreenshot: DesktopOcrView | null;
  latestOcr: DesktopOcrView | null;
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
  desktop?: DesktopView | null;
}

export interface ReplayEvent {
  index: number;
  timestamp: string;
  type: string;
  capability: string | null;
  status: string | null;
  summary: string;
  errorType: string | null;
  verificationOk: boolean | null;
  payload?: Record<string, unknown>;
}

export interface ReplayTimeline {
  taskId: string;
  objective: string;
  source: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  events: ReplayEvent[];
}

export interface TaskSummaryView {
  taskId: string;
  objective: string;
  source: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  actionCount: number;
  failureCount: number;
  approvalCount: number;
  denialCount: number;
  pendingApprovals: number;
  workflows: string[];
  lastCapability: string | null;
  // Checkpoint 11: present when the bridge merges live + restored
  // history. "session" = live in this process; "history" = restored
  // from runtime/history/ on disk. Tasks restored from history can
  // additionally be flagged interrupted if they were in-flight or
  // had a pending approval at the prior shutdown — the underlying
  // approval id is NOT carried across, by design.
  origin?: "session" | "history";
  interrupted?: boolean;
  interruptedReason?: string | null;
}

export interface HistoryHealthView {
  status: "ok" | "rebuilt" | "untrusted" | "unwritable";
  reason: string | null;
  schemaVersion: number;
  lastLoadedAt: string | null;
  lastWriteAt: string | null;
  writeError: string | null;
  restoredTaskCount: number;
  trusted: boolean;
}

export interface CapabilityCounter {
  executed: number;
  failed: number;
  blocked: number;
  awaiting: number;
}

export interface ReliabilityCounters {
  byCapability: Record<string, CapabilityCounter>;
  totals: {
    tasks: number;
    actions: number;
    failures: number;
    approvals: number;
    denials: number;
    memoryProposed: number;
    memoryApproved: number;
    memoryRejected: number;
    memoryExpired: number;
  };
  workflows: Record<string, { completed: number; failed: number }>;
}

export interface EventLogHealth {
  ok: boolean;
  recordCount: number;
  lengthBytes: number;
  lastEventAt: string | null;
  logPath: string;
  error?: string | null;
  // Checkpoint 11: present when the bridge runs with a HistoryStore.
  // When `history.trusted === false` the HUD shows that restored
  // history is not authoritative (the audit chain failed verify or
  // history files were rebuilt from corruption).
  history?: HistoryHealthView | null;
}

export type StructuredCapability =
  | "browser.navigate"
  | "browser.read_page"
  | "browser.summarize"
  | "browser.current_page"
  | "filesystem.read"
  | "filesystem.list"
  | "filesystem.write"
  | "app.launch"
  | "app.focus"
  | "desktop.clipboard_read"
  | "desktop.clipboard_write"
  | "desktop.notify"
  | "desktop.foreground_window"
  | "desktop.screenshot_foreground"
  | "desktop.screenshot_full"
  | "desktop.ocr_foreground"
  | "desktop.ocr_full"
  | "desktop.ocr_screenshot";

