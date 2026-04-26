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
