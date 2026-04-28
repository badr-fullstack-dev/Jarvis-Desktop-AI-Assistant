# Project history — shipped work

Every task below was shipped as one bundled checkpoint (backend + HUD + tests + README), per the checkpoint workflow. Order is chronological.

---

## Checkpoint 1 — Initial scaffold

### Plan
- [x] Guarded-autonomy Python supervisor (policy engine, action gateway, signed event log).
- [x] Memory store + lesson proposal scaffolding.
- [x] Stub capability adapters for browser, filesystem, applications.
- [x] React/Tauri HUD shell showing plans, subagents, approvals, memory, trace.
- [x] JSON schemas + default guarded-autonomy policy.
- [x] First unit tests for the secure core runtime.

### Review
Laid the non-negotiable floor: never bypass ActionGateway/PolicyEngine; Tier 2 approval-gated, Tier 3 blocked. Stubs were honest placeholders.

---

## Checkpoint 2 — Live bridge, real capabilities, action loop, voice layer

### Plan
- [x] Stdlib HTTP bridge on `127.0.0.1:7821` (`bridge.py`, `__main__.py`) + Tauri reqwest commands.
- [x] Endpoints: `/hud-state`, `/tasks`, `/tasks/<id>/trace`, `/memory`.
- [x] Real Windows-first capability adapters (browser / filesystem / applications) with scope roots, sandbox writes, dry-run, post-flight verification.
- [x] End-to-end action loop: `/actions/propose|execute|deny`, `/approvals`, `/actions/<id>`; supervisor tracks pending approvals + action results.
- [x] HUD `ActionPanel` with working Approve/Deny buttons; `latestResult` surfaced in `/hud-state`.
- [x] Push-to-talk voice layer: `VoiceSession` state machine (idle → recording → transcribing → ready → idle), `TranscriptionProvider` ABC with a clearly-labelled stub default.
- [x] HUD `VoicePanel` with MediaRecorder-based PTT and transcript preview; optional offline TTS via `window.speechSynthesis`.
- [x] 57 unittest cases across runtime, bridge, capabilities, action loop, voice.

### Review
First checkpoint where a human could drive the system end-to-end from the HUD. Privacy boundaries documented: stub transcription is not real ASR; mic is PTT-only.

---

## Checkpoint 3 — Deterministic planner

### Plan
- [x] `DeterministicPlanner` in `planner.py`: regex-based, LLM-free intent → structured `ActionProposal`.
- [x] Supported intents: `browser.navigate`, `browser.read_page`, `filesystem.read`, `filesystem.list`, `filesystem.write`, `app.launch`.
- [x] URL normalization (add missing `https://`), app allowlist mirror, sandbox-only write guard, Windows drive-letter path support.
- [x] Ambiguity paths: deictic targets ("open it"), unknown apps, bare-word reads → `clarification_needed`.
- [x] `LocalSupervisorAPI.submit_voice_or_text_task` auto-proposes the planner result through the gateway.
- [x] HUD `PlanPanel` showing rule, parameters, confidence, and decline reasons.
- [x] `plan.evaluated` event on the signed audit log.
- [x] 25 planner unit tests + 7 planner integration tests.
- [x] README "Deterministic planner (v1)" section.

### Review
Proved we can map natural language onto structured actions without inventing anything. Every mapping still flows through the existing guarded path.

---

## Checkpoint 4 — Browser context (v1)

### Plan
- [x] Thread-safe in-memory `BrowserContext` (url/title/textExcerpt/byteCount/source/updatedAt).
- [x] Extend `BrowserCapability` with `browser.summarize` and `browser.current_page` (both Tier 0); shared `_fetch_page`; stdlib HTML→text extraction; deterministic first-N-sentences summarizer.
- [x] Populate context on every `browser.read_page` / `browser.summarize(url)`.
- [x] Planner rules: "summarize this page", "summarize <url>", "what page am I on?", "open <url> and read it", "read this page" (context-aware routing).
- [x] HTTP bridge endpoints: `GET /browser/context`, `POST /browser/snapshot`, `POST /browser/clear`.
- [x] Tauri commands + HUD `BrowserPanel` (context display, manual snapshot form, clear).
- [x] Size caps: 512 KB fetch, 4 KB excerpt, 300-char title; `<script>/<style>` stripped; no DOM/JS.
- [x] 22 new tests (unit + loopback HTTP + planner + end-to-end).
- [x] README "Browser context (v1)" with honest limitations table.

### Review
Assistant can now safely answer context-relative requests ("read/summarize this page", "what page am I on?") without touching real browser tabs or running JS. 140 tests.

---

## Checkpoint 5 — Bounded multi-step workflows

### Plan
- [x] `workflow.py` with `Workflow`, `WorkflowStep`, `WorkflowPlan`, `WorkflowPlanner`, `WorkflowRunner`.
- [x] States — workflow: `queued`, `in_progress`, `waiting_for_approval`, `blocked`, `completed`, `failed`. Step: `pending`, `running`, `waiting_for_approval`, `completed`, `failed`, `blocked`, `skipped`.
- [x] Four v1 patterns: `wf.open_and_read`, `wf.open_and_summarize`, `wf.read_then_summarize`, `wf.write_then_read`.
- [x] Runner drives each step via `SupervisorRuntime.propose_action` — no new execution engine.
- [x] Approval pause: step → `waiting_for_approval`, workflow halts; approval → supervisor executes, runner advances; denial → step and workflow `failed`.
- [x] `api.approve_and_execute` / `api.deny_approval` wrappers keep supervisor and workflow in sync.
- [x] `workflow` field in hud-state; bridge trace summaries for `workflow.*` events.
- [x] HUD `WorkflowPanel` (pattern id, current step, live step list, per-step errors).
- [x] 17 tests: planner, runner with fake `propose_fn` (happy path, pause, resume, denial, block, step failure), end-to-end via `LocalSupervisorAPI` for all four patterns, unsupported → fallback to single-step.
- [x] README "Bounded workflows (v1)" with supported patterns, states, approval semantics, and honest limitations.

### Review
Assistant can now execute short, finite, inspectable sequences. Nothing improvised, no loops. 157 tests. Nothing bypasses ActionGateway/PolicyEngine.

---

## Checkpoint 6 — Windows desktop control (v1) (started 2026-04-21)

### Plan
- [x] New `DesktopCapability` adapter (stdlib ctypes, Windows-first):
  - [x] `desktop.clipboard_read` (Tier 0) — read CF_UNICODETEXT via user32.
  - [x] `desktop.clipboard_write` (Tier 1) — GlobalAlloc + SetClipboardData.
  - [x] `desktop.notify` (Tier 1) — non-blocking MessageBoxW in a daemon thread; honest dialog-notification.
  - [x] `desktop.foreground_window` (Tier 0) — GetForegroundWindow + GetWindowTextW + PID → exe path.
- [x] Real `app.focus` — enumerate top-level windows, match allowlisted exe path via OpenProcess+QueryFullProcessImageNameW, ShowWindow(SW_RESTORE)+SetForegroundWindow; honest failure when process not running or SetForegroundWindow is restricted.
- [x] Policy entries + scopes for all four desktop capabilities.
- [x] Planner mappings: clipboard read/write, notification, foreground window, focus-allowlisted-app.
- [x] Two new workflow patterns: `wf.open_and_focus`, `wf.copy_and_notify`.
- [x] HUD: `DesktopPanel` showing latest clipboard/foreground/notification/focus result; `desktop` field added to hud-state.
- [x] Tests: clipboard round-trip via injected hooks, notification dry-run + execute, focus allowlist rejection, focus when app not running, focus when Windows refuses, non-Windows honest failure, foreground window injection, planner mappings for each new intent. 28 new tests (185 total).
- [x] README "Windows desktop commands (v1)" with honest limitations table.

### Review
Shipped the new `DesktopCapability` adapter (stdlib ctypes only) with clipboard read/write, `MessageBoxW`-based notification, foreground-window introspection, and a real `app.focus` that enumerates top-level windows, matches the exe path against the existing app allowlist, and calls `SetForegroundWindow`. Everything still routes through ActionGateway/PolicyEngine; no raw shell, no keyboard/mouse automation. Non-Windows platforms fail honestly with `platform_unsupported` rather than faking it. The two new workflow patterns reuse the existing runner, so approval/denial mechanics are unchanged. 185 tests passing.

---

## Checkpoint 7 — Screen & UI awareness (v1) (started 2026-04-21)

### Plan
- [x] `desktop.screenshot_foreground` (Tier 0, scope `desktop.screen_read`) — capture foreground window via `PrintWindow`.
- [x] `desktop.screenshot_full` (Tier 0, scope `desktop.screen_read`) — capture the virtual screen via `BitBlt` on the desktop DC.
- [x] Stdlib PNG encoder (no Pillow dep): `struct` + `zlib.crc32` + `zlib.compress`.
- [x] Write PNG to `runtime/screenshots/<id>.png`; return `path`, `width`, `height`, `byte_count`.
- [x] Bridge: `GET /screenshots/<name>` serving the saved file with path-traversal guard (regex + resolved-parent match).
- [x] HUD: `DesktopPanel` shows latest screenshot preview via `http://127.0.0.1:7821/screenshots/<name>`.
- [x] Planner: "take a screenshot", "screenshot my window", "what is on my screen", "capture the entire desktop", etc.
- [x] Tests: injected capture_fn → real PNG on disk, dry-run, non-Windows honest failure, planner mappings, bridge endpoint path-traversal rejection. 15 new tests (200 total).
- [x] README "Screen & UI awareness (v1)" with privacy + limitations table (OCR explicitly deferred).

### Review
Shipped stdlib-only screen observation. `DesktopCapability` grew two new Tier 0 capabilities and a ~40-line PNG encoder (`struct` + `zlib.crc32` + `zlib.compress`); foreground capture uses `PrintWindow(PW_RENDERFULLCONTENT)`, full-screen uses `BitBlt(SRCCOPY | CAPTUREBLT)` across the virtual-screen rect from `GetSystemMetrics`. PNGs land under `runtime/screenshots/`. The bridge's `GET /screenshots/<name>` applies a strict `screenshot-<uuid>.png` regex AND re-resolves the final path to prove the parent equals the configured root — path traversal returns 404. The HUD panel now renders an `<img>` preview fetched from the bridge, with no base64 in the JSON payload. Nothing bypasses ActionGateway/PolicyEngine; no mouse/keyboard automation; no OCR (deferred — would need WinRT or a system Tesseract). Non-Windows platforms fail honestly with `platform_unsupported`. 200 tests passing.

---

## Checkpoint 8 — Local OCR (started 2026-04-27)

### Plan
- [x] `OCRProvider` ABC + `OCRError` + `UnavailableOCRProvider` (default — fails honestly with remediation hint, no fake text).
- [x] `WindowsMediaOCRProvider` — local OCR via `winsdk` (Windows.Media.Ocr). Lazy import; honest error if `winsdk` is missing or no language packs are installed.
- [x] `build_ocr_provider_from_env(JARVIS_OCR_PROVIDER, JARVIS_OCR_LANGUAGE)` with `auto` chain (windows-media-ocr → unavailable).
- [x] Three new capabilities on `DesktopCapability`, all Tier 0 / scope `desktop.screen_read` — foreground/full/screenshot.
- [x] Structured OCR output (mode, screenshot meta, text, lines, byte/char/line counts, average_confidence=null, language, provider, dry_run).
- [x] Caps: 64 KB extracted text, 5,000 lines; honest pre-truncation counts preserved.
- [x] Policy entries for all three OCR capabilities.
- [x] Planner rule for OCR (runs before screenshot rule). All supported phrasings unit-tested.
- [x] `LocalSupervisorAPI` constructs OCR provider from env and passes it into `DesktopCapability`.
- [x] Bridge registers OCR caps in `_DESKTOP_CAPS`; `_build_desktop_view` exposes `latestOcr`, `ocrForeground/Full/Screenshot`, and reuses OCR's source screenshot for `latestScreenshot` when no plain capture is more recent.
- [x] HUD: `DesktopOcrView` contract; `DesktopPanel` renders OCR card with semantic markup reviewed by accessibility-lead (aria-labelledby/aria-describedby on the focusable `<pre>`, decorative alt on the duplicate-content image, role="status" on the truncation warning, sanitised id fragments).
- [x] 29 OCR tests added (provider builder, composite, adapter foreground/full/screenshot, dry-run, non-Windows, provider failure, path traversal, missing file, truncation, line cap, planner mappings, end-to-end via API, bridge view via HTTP).
- [x] README "Local OCR (v1)" section with capability table, output shape, planner phrasings, setup, env vars, end-to-end test plan, privacy model, and an honest-limitations table (no confidence, language-pack dependence, GPU-composed surfaces, scope of `desktop.ocr_screenshot`).
- [x] Full suite — 229 tests passing.

### Review
Shipped Windows-first local OCR. New `ocr_providers.py` mirrors the voice-provider shape (ABC + Unavailable + WindowsMediaOCR + Composite + env builder) with a clear contract: missing dependencies raise `OCRError` with actionable instructions and we never fabricate text. `DesktopCapability` grew three Tier-0 capabilities — foreground/full *capture-and-OCR* in one call (which is what "take a screenshot and read it" really means, so no workflow needed), plus `desktop.ocr_screenshot` for re-OCRing an existing PNG by name (regex + parent-resolve guard, same hardening as the bridge endpoint). `winsdk` is an opt-in pip dep; the default provider is `unavailable` so a fresh checkout has zero OCR until the user explicitly enables it.

Honest limits called out in the README: Windows.Media.Ocr does not expose per-word/line confidence (we report `null`, not a fake number); recognition depends on installed language packs; DRM-protected surfaces stay black. Nothing bypasses ActionGateway/PolicyEngine; no mouse/keyboard automation introduced; no background OCR. 229 tests passing.

Accessibility-lead reviewed the HUD card before any TSX was written. Their corrections went straight into the implementation: dropped invalid `<label htmlFor>` for `aria-labelledby` on the focusable `<pre>`; dropped redundant `aria-label` (would have masked the text content); decorative `alt=""` on the OCR source image (text already exposed below); `role="status"` instead of `role="note"` on the truncation warning so it announces; sanitised the timestamp-derived id fragment.

---

## Checkpoint 9 — Curated memory & reflection (started 2026-04-27)

### Plan
- [x] `MemoryStore` extended with `propose` / `approve` / `reject` / `expire` / `delete` / `get`. Status lifecycle persisted (`reviewed_at`, `reviewed_by`, `review_reason`).
- [x] New `reflection.py` with `Reflector` + `is_sensitive_payload`. Four proposal patterns: tool failures, planner clarifications, completed workflows, explicit-preference objectives. De-dup within a task.
- [x] Sensitive-payload filter rejects clipboard / OCR / transcript / screenshot text and free-form summaries longer than 800 chars. Enforced inside `MemoryStore.propose` so no caller can bypass.
- [x] Supervisor: replaced naive `_curate_lessons` with a Reflector call after every action result (executed/blocked/failed); per-task dedup keeps the trace clean. New `approve_memory` / `reject_memory` / `expire_memory` wrappers log every transition on the signed event log.
- [x] `DeterministicPlanner` accepts a `memory_hint_provider`. `PlanResult.memoryHints` is advisory only — never changes capability, parameters, or confidence.
- [x] `LocalSupervisorAPI` wires `ApprovedMemoryHints(self.memory)` into the planner.
- [x] Bridge endpoints: `GET /memory?status=&kind=`, `GET /memory/proposals`, `POST /memory/{id}/approve`, `POST /memory/{id}/reject`, `POST /memory/{id}/expire`.
- [x] Tauri commands `memory_approve` / `memory_reject` / `memory_expire` / `memory_proposals`.
- [x] HUD: new `MemoryPanel` with Pending / Approved / Recent groups + Approve / Reject / Expire actions; live-region toast for action results. `PlanPanel` shows a memory-hint live region when relevant. Accessibility-lead reviewed markup before TSX shipped — feedback applied (no `aria-label` masking content, `role="status"` for live updates, `aria-disabled` over `disabled`, no `<header>` inside `<li>`, no noisy group `aria-label` repetitions).
- [x] 34 new tests added (memory lifecycle, sensitive-payload filter, reflector emissions for each pattern, dedup, ApprovedMemoryHints scoping by capability/rule, memory-does-not-bypass-policy via real `LocalSupervisorAPI`, bridge endpoints round-tripping approve / reject / expire / 404). Existing `test_executed_action_proposes_lesson` rewritten to match the new reflector contract; new `test_clean_action_does_not_spam_lessons` verifies the old boilerplate behaviour is gone.
- [x] README "Memory & reflection (v1)" section: layers table, lifecycle diagram, reflection patterns, sensitive-payload filter, planner-hint contract, HUD controls, bridge endpoints, end-to-end test recipe, what memory CAN/CANNOT influence, honest privacy limitations.
- [x] Full suite — 263 tests passing.

### Review
Curated memory shipped without giving the assistant any new way to act on its own. Memory `propose` is locked behind a sensitive-payload filter that catches clipboard/OCR/transcript/screenshot text and free-form summaries; `Reflector` emits at most four kinds of structural metadata (capability + error_type, matched_rule, pattern_id, preference phrase) and dedupes within a task. Approval is the only path from candidate → approved, and the planner is wired so memory hints can only annotate a plan — they cannot change capability, parameters, confidence, or the PolicyEngine's tier decision (verified by an end-to-end test that approves a "user prefers filesystem.move without approval" profile memory and asserts Tier 2 still queues an approval).

Accessibility-lead reviewed the new `MemoryPanel` and `PlanPanel` memory-hint markup before any TSX shipped. Their must-fix items applied directly: dropped the noisy `aria-label="Actions for memory <summary>"` repetition; replaced `role="note"` with `role="status"` on the plan memory-hint live region (`role="note"` doesn't announce on update); dropped the stray `<header>`-inside-`<li>` pattern in favour of a plain `div`; switched to `aria-disabled` + click-guard so focus is preserved through async transitions.

No follow-ups left for this checkpoint. The next natural extensions are (a) letting the planner re-rank ambiguous interpretations using approved lessons, and (b) tying memory rows back to the specific event-log entries they came from for true bidirectional audit.

---

## Checkpoint 10 — Replay, reliability, and audit review (started 2026-04-27)

### Plan
- [x] New `reliability.py` module: `task_replay`, `task_summary`, `recent_task_summaries`, `reliability_counters`, `event_log_health`. Read-only over `task.trace` and the signed event log; never mutates either.
- [x] Privacy redactor (`_scrub_dict`) blocks clipboard/OCR/transcript/screenshot/file-content keys, summarises line/word lists to `{count: N}`, clips summaries and objectives to 200 chars. Idempotent.
- [x] Bridge endpoints: `GET /tasks?limit=`, `GET /tasks/{id}/replay`, `GET /reliability/health`, `GET /reliability/counters`. Tauri commands `list_recent_tasks`, `fetch_replay`, `reliability_health`, `reliability_counters`.
- [x] HUD: new `ReplayPanel` with recent-tasks list, redacted timeline, capability counter table (with red flag on non-zero failures/blocks), and an event-log health badge. Accessibility-lead reviewed markup before TSX shipped — feedback applied (`role="alert"` for tamper, `aria-current` instead of `aria-pressed`, scoped table headers, no `aria-label` masking content).
- [x] 25 new tests across redaction (every sensitive key family), replay shape & ordering, summary rollups, counter aggregation across tasks, event-log health (fresh / appended / tampered / mutation-free), and bridge endpoint round-trips. Full suite — 288 tests passing.
- [x] README "Replay & reliability (v1)" section: review surface, endpoints, redaction list, what is NOT redacted (and why), honest limits.

### Review
Shipped a read-only review/diagnostics layer that turns the existing trace + signed event log into a navigable replay surface — without giving the assistant any new capability to act. Every replay event passes through a deterministic redactor that strips clipboard/OCR/transcript/screenshot/file-content payloads but keeps the diagnostic skeleton (capability, status, error_type, verification keys, IDs, decision metadata). The health badge surfaces `verify_chain` integrity assertively (`role="alert"` on tamper) without ever modifying the log. Counters are session-scoped by design; cross-session aggregation off the persistent JSONL is left for a follow-up.

Accessibility-lead reviewed the new panel before TSX was written. Their must-fix items applied directly: split the health badge so the tamper string uses `role="alert"` (assertive interrupt for a security signal) while the healthy path stays `aria-live="polite"`; switched the recent-tasks selector from `aria-pressed` to `aria-current="true"` since this is "current item in a set" not a toggle; kept the canonical `<th scope="col">` / `<th scope="row">` table pattern with an `sr-only` caption; verified heading levels match sibling panels.

No follow-ups left for this checkpoint. Natural next steps: (a) parse persistent `runtime/events.jsonl` so counters survive restarts, (b) add an export-to-JSONL button on a single replay for offline review, (c) signed-log compaction with rotated chains.

---

## Checkpoint 11 — Durable session history & restart-safe replay (started 2026-04-28)

### Plan

#### Hard rules (re-state before I touch anything)
- No auto-resume / no auto-execute after restart.
- Pending approvals from a previous process come back as "interrupted" — never as live executable buttons.
- No raw user content to disk: clipboard bodies, OCR text, transcripts, screenshots bytes, audio, file-write content, browser excerpts, unredacted capability outputs.
- Reuse the same `_HARD_REDACT_KEYS` set as `reliability.py` / `reflection.py`. If a new user-content key comes up, add it to **both** redactors.
- Signed event log stays the source of audit truth. `runtime/history/` is *derived* and rebuildable.
- If `verify_chain()` fails on startup, history is marked **untrusted** — `/reliability/health` says so and the HUD shows it.

#### Backend: new `history.py` module
- [x] `services/orchestrator/src/jarvis_core/history.py`. Schema-versioned (`{"schema_version": 1, ...}`).
- [x] Files under `runtime/history/`:
  - `tasks.json` — newest-first list of redacted task summaries.
  - `replays/<task-id>.json` — one redacted replay timeline per task.
  - `counters.json` — persisted reliability counters.
  - `state.json` — `{health: ok|untrusted|rebuilt, last_event_signature, schema_version, generated_at}`.
- [ ] Atomic writes: write to `*.tmp` then `os.replace`. fsync on best-effort.
- [ ] Strict task-id validation (UUID-ish only — already enforced by `models.new_id`; reject anything else when reading filenames). Never path-join user-supplied strings.
- [ ] `record_task_update(task)` and `record_counters(...)` — used by the supervisor hook. Both call into the existing `_scrub_dict` / `task_replay` / `task_summary` / `reliability_counters` so we *cannot* re-implement redaction with weaker rules.
- [ ] `load_history(runtime_path)` returns `HistorySnapshot(state, tasks, replays_index, counters)`. Corrupt JSON → return empty snapshot + state.health = "rebuilt" with a clear reason. Schema mismatch → same.
- [ ] `prune(limit=200)` — keep newest N task entries; drop older `replays/<id>.json` files. Ship with a sane default.

#### Persisting redacted data as tasks run
- [ ] Hook into `SupervisorRuntime` via a new `HistoryRecorder` callback (one-line `task.touch()` extension OR an explicit `supervisor.notify_task_changed(task)` call). I'll prefer the explicit notify — it keeps the recorder out of the hot path of every `task.touch()`.
- [ ] Recorder updates `tasks.json` and `replays/<id>.json` on every meaningful trace change, debounced if needed (atomic write per change is fine for v1; debouncing is a follow-up if it shows up in profiling).
- [ ] Recorder runs the same `_scrub_dict` already used by `task_replay`. Do not duplicate raw `TaskRecord` objects to disk — only the output of `task_replay()` and `task_summary()`.
- [ ] Workflow status that's `waiting_for_approval` or `running` at write time gets persisted with `interrupted_marker: false`. On startup the loader flips that to `interrupted_marker: true` for any workflow not also live in memory.

#### Startup behavior in `LocalSupervisorAPI.__init__`
- [ ] After constructing `event_log`, call `event_log.verify_chain()`.
- [ ] If healthy: `load_history()` → store on `self.history`. `state.health = "ok"`.
- [ ] If history missing/corrupt/schema-mismatch (but log healthy): minimal rebuild from `events.jsonl` if practical; otherwise start empty with `state.health = "rebuilt"` and a reason string. **Never silently overwrite a clean history with an empty one.**
- [ ] If log unhealthy: `state.health = "untrusted"`. Do NOT trust history. `/reliability/health` returns ok=false, history.trusted=false, with the verifier reason.
- [ ] Pending approvals from supervisor.tasks at startup are empty (they're session-scoped). History-only tasks never expose executable approval buttons — that's by construction since approval IDs are not persisted as actionable.

#### Cross-session reliability counters
- [ ] `reliability_counters_combined(session_tasks, history)` — merges in-memory counters with persisted counters. Adds `source: "session" | "history" | "mixed"` so the HUD is honest.
- [ ] `/reliability/counters` returns the combined view by default.

#### Bridge endpoints (stable surface, no breaking changes)
- [ ] `GET /tasks?limit=N` — merges live and historical, dedup by task_id, newest first.
- [ ] `GET /tasks/{id}/replay` — prefer in-memory `TaskRecord`; fall back to `runtime/history/replays/<id>.json`. Same redacted shape either way.
- [ ] `GET /reliability/counters` — combined view, plus `source` field.
- [ ] `GET /reliability/health` — extended with `history: {trusted, source, schema_version, last_loaded_at}`.

#### HUD updates (TSX edits — accessibility-lead pre-review required)
- [ ] Tag each task in the recent list with a tiny badge: `current` | `restored` | `interrupted`. Use `<span>` with `aria-label` set so screen readers don't drop the meaning.
- [ ] When `health.history.trusted === false`, show the existing tamper alert + a short note: "Restored history is not trusted while audit log verification fails." (`role="alert"` on the security signal, polite live region for the explanation.)
- [ ] No new panels. Just badges + the additional health note. Keep noise low.
- [ ] **Pre-delegate to accessibility-lead before any TSX edit**, with the concrete markup. Apply must-fixes before merging.

#### Dev-watch
- [ ] Add `history.py` to the watched module list. The existing module-discovery code already globs `jarvis_core/*.py`, so verify that — if it doesn't, extend it.
- [ ] Document the dev loop in README: `python -m jarvis_core.dev_watch` watches all backend modules; bridge restart is automatic on save. Manual restart only required for `dev_watch.py` itself or external tooling.

#### Tests (Python `unittest`, not pytest)
- [ ] `test_history_redaction.py` — every `_HARD_REDACT_KEYS` family is stripped from `tasks.json` and `replays/<id>.json`. Idempotency: reload + re-save, no drift.
- [ ] `test_history_atomic.py` — simulate write crash mid-`*.tmp`, confirm the final file is untouched.
- [ ] `test_history_restart.py` — submit tasks, tear down `LocalSupervisorAPI`, build a new one against the same runtime root, recent tasks survive, replay endpoint works for restored task.
- [ ] `test_history_counters.py` — counters persist across restart and `source` field flips appropriately.
- [ ] `test_history_tamper.py` — corrupt last line of `events.jsonl`, restart, expect `state.health == "untrusted"`, `/reliability/health.history.trusted == false`, history not silently rebuilt.
- [ ] `test_history_corrupt.py` — write a bad `tasks.json`, restart, expect graceful empty state and a clear reason, not a crash.
- [ ] `test_history_interrupted_workflow.py` — workflow `waiting_for_approval` at write time, restart, comes back as `interrupted` with no executable approval button (verified at the bridge JSON layer).
- [ ] `test_history_no_user_content.py` — task with clipboard/OCR/transcript/file-content/screenshot payloads in trace, restart, ensure raw bytes/text are nowhere in `runtime/history/**`. Grep-style assertion over the JSON.
- [ ] All existing 294 tests still pass.

#### Documentation
- [ ] README new section "Durable history & restart safety (v1)":
  - what persists (redacted summaries, replay timelines, counters);
  - what does NOT auto-resume (actions, approvals, workflows);
  - the difference between the signed audit log and derived history;
  - how to safely reset derived history (`rm -rf runtime/history/`) without harming the audit log;
  - dev_watch loop and the rare cases where manual restart is needed.

#### Order of work
1. Brainstorm/design `history.py` module + state schema. Land tests `test_history_redaction.py`, `test_history_atomic.py`, `test_history_corrupt.py` first (TDD on the data layer).
2. Add the `HistoryRecorder` hook into `LocalSupervisorAPI` + `SupervisorRuntime`. Land `test_history_restart.py`, `test_history_no_user_content.py`, `test_history_counters.py`.
3. Tamper / unhealthy-log flow + `/reliability/health` extension. Land `test_history_tamper.py`.
4. Workflow interrupted flow. Land `test_history_interrupted_workflow.py`.
5. Bridge endpoint changes (combined view).
6. Pre-delegate to accessibility-lead with the concrete badge markup. Apply feedback. Then HUD TSX edits.
7. README section.
8. Run full suite locally on Windows + push and verify CI green.

### Review

Shipped a redacted, derived history layer at `services/orchestrator/src/jarvis_core/history.py`, hooked through a new `SupervisorRuntime.notify_task_changed(task)` callback set by `LocalSupervisorAPI`. Every task-mutation site (`submit_task`, `request_action`, `propose_action`, `approve_and_execute`, `deny_approval`, `cancel_task`, `resume_task`, plus the workflow trace mutations in `api.submit_voice_or_text_task` / `approve_and_execute` / `deny_approval`) calls the recorder. The recorder feeds `task_summary` and `task_replay` through the central `_scrub_dict` redactor and writes atomic JSON under `runtime/history/`. Bridge endpoints `/tasks`, `/tasks/{id}/replay`, `/reliability/counters`, `/reliability/health` were extended in place — same URLs, additive shape with `origin`, `interrupted`, `source`, `historyTrusted`, `currentSessionTaskCount`, `restoredTaskCount`. ReplayPanel.tsx gained `restored` / `interrupted` plain-text badges (no color-only signaling, 4.5:1 text + 3:1 chip border per a11y review) and an unconditionally-rendered polite live region that surfaces "Restored history is not trusted while audit-log verification fails." when `verify_chain` fails. README gained a 100-line "Durable history & restart safety (v1)" section covering what persists, what does not auto-resume, the audit-log-vs-history table, the safe reset command, and the dev_watch loop.

Hard rules verified by tests (17 new across `test_history.py`, full suite 311/311 OK):
- redaction over every `_HARD_REDACT_KEYS` family — no raw clipboard, OCR, transcript, audio, screenshot bytes, file content, or excerpt text leaks into `runtime/history/**`;
- atomic write — `os.replace` failure leaves the live file untouched and no orphan `*.tmp`;
- corrupt `tasks.json` / schema mismatch / hostile filename → graceful empty + `state.health = "rebuilt"`;
- tampered `events.jsonl` → `state.health = "untrusted"`, history not silently rebuilt;
- pending approval at write-time → restored as `interrupted`, `pendingApprovals = 0`, no executable approval id carried across;
- counter merge across session/history with the mixed policy returns the right `source` and `restoredTaskCount`/`currentSessionTaskCount`.

What got cut for v1:
- explicit log-replay rebuild path (the loader already starts empty on corruption — log-driven rebuild is left for a follow-up checkpoint that wants exact counter recovery across deleted history);
- debounced writes (current path writes per task mutation, which is fine at the supervisor task volume — revisit if profiling shows it);
- exposing `history.status` / `lastWriteAt` directly in the HUD beyond the existing tamper alert (the polite note covers the user-visible case; the rest is on `/reliability/health` for debuggers).

Accessibility-lead pre-review of the badge + live-region markup applied verbatim: badges are plain-text words inside the existing `<button>` so the accessible name reads as one stop; the polite `<p>` is rendered unconditionally with empty text when healthy (sr-only) so the assertive tamper alert does not race the polite supplementary note; chip border drawn from `currentColor` keeps the chip shape perceivable for low-vision users. No `aria-hidden`, no `aria-disabled`, no color-only signaling.

Natural follow-ups: (a) audit-log-driven rebuild for installs that wipe `runtime/history/` but keep `events.jsonl`; (b) a "history reset" button on the panel that confirms before deleting, instead of requiring a shell command; (c) HUD-side dedicated "Interrupted tasks" subsection if the restored-task volume gets large enough to need it.

---

## Ongoing / future

See the "Next recommended step" list in `README.md`. Currently open:
- Real browser-automation channel (CDP or WebView2).
- Wake word behind an explicit privacy mode.
- Expand the verifier and replay/eval harnesses before increasing autonomy.

## Template for future tasks

```
## <task title> (started YYYY-MM-DD)

### Plan
- [ ] step 1
- [ ] verification step

### Review
_Filled in when task is complete: what shipped, what was cut, follow-ups._
```
