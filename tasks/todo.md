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

## Ongoing / future

See the "Next recommended step" list in `README.md`. Currently open:
- Real browser-automation channel (CDP or WebView2).
- `app.focus` via UIA so focus works on already-running processes.
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
