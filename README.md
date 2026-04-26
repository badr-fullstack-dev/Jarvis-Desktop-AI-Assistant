# Jarvis Guarded Desktop Assistant

This repository is the first working checkpoint for a Windows-first desktop AI assistant with guarded autonomy, a futuristic HUD, and a multi-subagent runtime.

## What is implemented

- Monorepo structure for a Tauri-style HUD, Rust security services, shared schemas, and a Python orchestrator.
- A Python core runtime with:
  - policy engine and risk tiers,
  - action gateway with approval gating,
  - append-only signed audit log,
  - supervisor runtime,
  - blackboard-style task coordination,
  - memory layers and lesson proposals,
  - stub capability adapters for browser, filesystem, and applications.
- A React/Tauri HUD shell that visualizes plans, subagents, approvals, memory, and recent traces.
- JSON schemas and default policies for future cross-language validation.
- Unit tests for the secure core runtime.
- A staged prompt pack to keep implementation work sequenced and safe.

## Current limitations

- Rust tooling is not available in the current sandbox, so the Rust/Tauri pieces are scaffolded but not compiled here.
- Voice has a real **local** STT path (faster-whisper / whisper.cpp, off by default). Wake word and cloud STT are intentionally not wired up.
- Capabilities are conservative by design; Tier 2 actions require approval and Tier 3 remain blocked.
- No in-process browser automation yet — `browser.read_page` is a plain HTTP fetch, `browser.navigate` hands the URL to the OS default browser.

## Live HUD ↔ Python bridge

The HUD is now connected to the real Python orchestrator through a small local HTTP bridge (stdlib only, no network exposure — binds to `127.0.0.1:7821`).

**Architecture**

```
React HUD  ──invoke()──▶  Tauri (Rust, reqwest)  ──HTTP──▶  Python bridge (stdlib)
                                                             │
                                                             ▼
                                                    LocalSupervisorAPI
                                                    (PolicyEngine → ActionGateway
                                                     → SignedEventLog + MemoryStore)
```

Every action still flows through the `ActionGateway` + `PolicyEngine` — the bridge only exposes read-only snapshots and task submission. No raw shell execution is introduced.

**Endpoints** (`services/orchestrator/src/jarvis_core/bridge.py`)

| Method | Path                         | Purpose                                    |
|-------:|------------------------------|--------------------------------------------|
|  GET   | `/health`                    | Liveness probe                             |
|  GET   | `/hud-state`                 | Full HUD snapshot (incl. `latestResult`)   |
|  POST  | `/tasks` `{objective}`       | Submit a text task through the supervisor  |
|  GET   | `/tasks/{task_id}/trace`     | Fetch trace for a specific task            |
|  GET   | `/memory`                    | All memory items (profile/lesson/tool/…)   |
|  POST  | `/actions/propose`           | Propose a structured action (auto-executes Tier 0/1, else queues approval) |
|  POST  | `/actions/execute`           | Execute a previously-approved action       |
|  POST  | `/actions/deny`              | Deny a queued approval (optional reason)   |
|  GET   | `/approvals`                 | List currently-pending approvals           |
|  GET   | `/actions/{action_id}`       | Fetch the stored result for an action      |
|  GET   | `/voice`                     | Current voice session snapshot             |
|  POST  | `/voice/start`               | Enter `recording` state                    |
|  POST  | `/voice/stop` `{audio_base64, mime}` | End recording, run provider, enter `ready` |
|  POST  | `/voice/submit` `{transcript?}` | Create a task from the pending transcript |
|  POST  | `/voice/discard`             | Drop a `ready` transcript, return to idle  |
|  POST  | `/voice/reset`               | Force session back to `idle`               |
|  POST  | `/voice/enable` `{enabled}`  | Enable/disable the microphone globally     |

**Tauri commands** (`apps/hud/src-tauri/src/main.rs`): `get_hud_state`, `submit_task`, `fetch_trace`, `fetch_memory`, `bridge_health`, `propose_action`, `execute_action`, `deny_action`, `list_approvals`, `fetch_action`, `voice_state`, `voice_start`, `voice_stop`, `voice_submit`, `voice_discard`, `voice_reset`, `voice_enable`.

If the bridge is not running, the HUD shows a visible "Bridge offline" banner and disables the task form, but keeps the last-known state visible.

## Running the full flow

Open two terminals from the project root.

**Terminal 1 — start the Python bridge:**

```powershell
python -m jarvis_core
# listens on http://127.0.0.1:7821
```

**Terminal 2 — start the Tauri HUD (from `apps/hud/`):**

```powershell
cd apps\hud
npm install        # first run only
npm run tauri dev  # requires the Rust toolchain
```

Submit a task from the HUD text box; the Subagents, Approvals, Memory, and Trace panels should refresh live every few seconds.

## Repository layout

- `apps/hud`: futuristic control surface for approvals, plans, memory, and traces
- `crates/security-bridge`: Rust policy/event primitives for future native integrations
- `services/orchestrator`: Python supervisor, gateway, memory, subagents, and tests
- `packages/schemas`: JSON contracts shared by Python, Rust, and the HUD
- `configs`: default guarded-autonomy policy configuration
- `docs`: architecture notes and staged roadmap
- `prompts/`: staged implementation prompts, one per build phase

## Running the Python tests

Run the Python test suite:

```powershell
python -m unittest discover -s services/orchestrator/tests -t services/orchestrator
```

## Capability adapters (v1)

Real Windows-first adapters now back the `ActionGateway`, still gated by the
`PolicyEngine`. **No raw shell execution** is introduced; every call goes
through a typed adapter.

| Capability              | Tier | What it really does                                           |
|-------------------------|:---:|---------------------------------------------------------------|
| `browser.navigate`      | 0   | `webbrowser.open()` — hands URL to the OS default browser.    |
| `browser.read_page`     | 0   | HTTP GET via urllib, extracts `<title>`, caps at 512 KB.      |
| `browser.download_file` | 2   | Approval-gated download into `runtime/sandbox/`, 10 MB cap.   |
| `filesystem.read`       | 0   | Reads metadata + ≤8 KB preview, scoped to workspace + sandbox.|
| `filesystem.list`       | 0   | Lists dir entries (≤500), scoped read roots only.              |
| `filesystem.search`     | 0   | `fnmatch` glob walk (≤200 matches), scoped.                    |
| `filesystem.write`      | 1   | Writes text (≤1 MB). **Destination must resolve inside sandbox_root.** |
| `filesystem.move`       | 2   | Approval-gated. Source in read roots, destination in sandbox. |
| `app.launch`            | 1   | Allowlisted launch (notepad, calc, explorer, mspaint) via `subprocess.Popen`. No arbitrary paths. |
| `app.focus`             | 1   | Raises an already-running allowlisted app via `SetForegroundWindow`. Fails honestly when not running or Windows refuses. |
| `app.install`           | 2   | Still intentionally unsupported — returns `failed: not_implemented` after approval. |
| `desktop.clipboard_read`  | 0 | Reads `CF_UNICODETEXT` from the clipboard via `ctypes`/`user32`. 4 KB excerpt cap. |
| `desktop.clipboard_write` | 1 | Writes UTF-16 text to the clipboard. 64 KB payload cap. |
| `desktop.notify`          | 1 | Non-blocking dialog notification (`MessageBoxW`) on a daemon thread. |
| `desktop.foreground_window` | 0 | Returns the current foreground window's title, PID, and exe path. |

Scope roots are configured automatically by `LocalSupervisorAPI`:
- **workspace_root** (passed to the API constructor) → read roots for `filesystem.*`.
- **sandbox_root** → `<workspace_root>/runtime/sandbox/` → the only writable location.

All adapters honor `proposal.dry_run=True` (returns what would happen without
touching the system) and provide a `verify()` postflight that is recorded in
the signed audit log alongside the execution event.

### Testing capabilities manually

The Python bridge exposes `POST /tasks` today; direct action submission
belongs in a later iteration. For now, exercise capabilities through
`LocalSupervisorAPI` in a short REPL / script:

```powershell
# From the project root
python -i -c "
import asyncio
from pathlib import Path
from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.models import ActionProposal

api = LocalSupervisorAPI(Path('.'))
task = asyncio.run(api.submit_voice_or_text_task('manual capability test'))

# Tier-0 safe: read a file inside the workspace
p = ActionProposal(task_id=task.task_id, capability='filesystem.read',
                   intent='read policy', parameters={'path': 'configs/policy.default.json'},
                   requested_by='me', evidence=['manual'], confidence=0.95)
print(api.submit_action(p).summary)

# Tier-1 safe write INSIDE sandbox (confidence>=0.85, not dry_run → auto-allowed)
w = ActionProposal(task_id=task.task_id, capability='filesystem.write',
                   intent='write hello', parameters={'path': 'runtime/sandbox/hello.txt', 'content': 'hi'},
                   requested_by='me', evidence=['manual'], confidence=0.95)
print(api.submit_action(w).summary)

# Tier-1 app launch (dry-run so nothing actually spawns)
a = ActionProposal(task_id=task.task_id, capability='app.launch',
                   intent='open notepad', parameters={'name': 'notepad'},
                   requested_by='me', evidence=['manual'], confidence=0.99, dry_run=True)
print(api.submit_action(a, approved=True).summary)
" -s services/orchestrator
```

(Set `PYTHONPATH=services\orchestrator` if you prefer to skip the `-s` trick;
the test discovery command in the next section does this automatically.)

### Running the full test suite

```powershell
python -m unittest discover -s services/orchestrator/tests -t services/orchestrator
```

This runs runtime + bridge + capability + action-loop + voice + planner
+ browser-context + workflow + desktop + screenshot + OCR + memory tests (263 tests total in this checkpoint). The capability tests use a local
loopback HTTPServer for browser tests and dry-run mode for
`app.launch`, so no external network or GUI processes are started.
Voice / STT tests inject deterministic providers, fake models, and
fake whisper.cpp runners — no microphone is opened, no model is
downloaded, and `ffmpeg` is never invoked.

## Voice layer (push-to-talk, v1)

The HUD now has a **push-to-talk voice interaction layer**. The rules
are strict and visible:

- **Push-to-talk only.** The microphone is only opened while you are
  holding the PTT button. Release = mic closed. There is no wake word,
  no continuous listening, and no hidden background capture.
- **Opt-in per session.** Voice is disabled by default. Tick
  *"Enable microphone"* in the Voice panel to opt in. Unticking it
  immediately resets the session to `idle`.
- **Transcript is always previewed before it becomes a task.** You
  can edit the transcript (or discard it) before *Submit as task* ever
  touches the supervisor.
- **Policy is unchanged.** A voice-submitted task enters the exact
  same `submit_task` → subagents → ActionGateway → PolicyEngine flow
  as a typed task. Voice cannot bypass approvals or tiers.

### Session state machine

Owned by the backend so the HUD can't silently advance it:

```
idle ──start()──▶ recording ──stop(audio)──▶ transcribing ──provider ok──▶ ready
                                                            └─provider fail─▶ error
ready ──submit()──▶ idle   (task created)
ready ──discard()─▶ idle
*     ──reset()───▶ idle
```

The snapshot (state + enabled flag + transcript preview + provider
name + last audio size + updatedAt) is included in every `/hud-state`
response.

### Transcription providers

`services/orchestrator/src/jarvis_core/voice.py` defines the
`TranscriptionProvider` ABC. The shipped implementations live in
`voice_providers.py`:

| Provider        | Name string        | Local? | Setup                                     |
|-----------------|--------------------|:------:|-------------------------------------------|
| Stub (default)  | `stub`             | —      | Nothing to install; returns a clearly-labelled synthetic transcript. |
| faster-whisper  | `faster-whisper`   | ✅     | `pip install faster-whisper` + `ffmpeg` on PATH. Model downloads on first use. |
| whisper.cpp     | `whisper.cpp`      | ✅     | Point at your pre-built `whisper-cli.exe` / `main.exe` + a `.bin` GGML/GGUF model. |
| Cloud STT       | —                  | ❌     | **Not shipped.** If you add one later, document audio leaves the machine and keep it off by default. |

The provider is chosen at bridge startup via environment variables
(read by `build_provider_from_env` in `voice_providers.py`):

| Variable                   | Values / examples                                    |
|----------------------------|------------------------------------------------------|
| `JARVIS_STT_PROVIDER`      | `stub` (default), `faster-whisper`, `whisper.cpp`, `auto` |
| `JARVIS_STT_MODEL`         | faster-whisper: `base.en` (default), `small.en`, `medium.en`. whisper.cpp: absolute path to `ggml-base.en.bin` etc. |
| `JARVIS_STT_MODEL_DIR`     | Optional local cache dir for faster-whisper downloads |
| `JARVIS_STT_COMPUTE`       | `int8` (default, CPU), `int8_float16`, `float16`     |
| `JARVIS_STT_DEVICE`        | `cpu` (default) or `cuda`                            |
| `JARVIS_STT_LANGUAGE`      | `en` (default). Empty string = auto-detect.          |
| `JARVIS_FFMPEG`            | Override ffmpeg path (default: `ffmpeg`)             |
| `JARVIS_WHISPERCPP_BIN`    | Absolute path to the whisper.cpp CLI binary           |
| `JARVIS_STT_DEBUG_DIR`     | If set, a failed decode dumps the raw HUD audio to this directory (`failed-<ts>.webm`) for offline repro. Off by default. |

`auto` is a fallback chain: faster-whisper first, stub last. Its
`provider` string in `/hud-state` reflects the chain honestly (e.g.
`faster-whisper+stub`), so you can see whether you're actually
getting real transcription. An unknown value raises a clear error at
startup rather than silently falling back.

#### Setup: faster-whisper on Windows (recommended)

1. **Install ffmpeg** and ensure it's on PATH. Either:
   - `winget install --id Gyan.FFmpeg`, or
   - Download from https://www.gyan.dev/ffmpeg/builds/ and add the
     `bin\` folder to your PATH.
   - Verify: `ffmpeg -version` prints a banner.
2. **Install faster-whisper** into the Python runtime you use for the
   bridge:
   ```powershell
   python -m pip install faster-whisper
   ```
3. **Pick a model.** `base.en` (≈140 MB) is a good starting point for
   short desktop-assistant commands; upgrade to `small.en` or
   `medium.en` for better accuracy at the cost of latency.
   The model downloads automatically on the first transcription call
   (network required once) and is cached under `~/.cache/huggingface`
   by default, or `JARVIS_STT_MODEL_DIR` if you set it.
4. **Start the bridge with faster-whisper enabled:**
   ```powershell
   $env:JARVIS_STT_PROVIDER = "faster-whisper"
   $env:JARVIS_STT_MODEL = "base.en"
   # Optional: $env:JARVIS_STT_MODEL_DIR = "C:\Users\badre\.cache\jarvis-stt"
   python -m jarvis_core
   ```
5. Open the HUD, tick *Enable microphone*, hold *Hold to talk*, and
   release. The Voice panel should now show
   `Transcription provider: faster-whisper` and produce a real
   transcript from your speech.

If anything is missing (ffmpeg, the pip package, or the model), the
HUD's Voice panel goes to `state="error"` with an actionable message
(e.g. `"faster-whisper is not installed. Run 'pip install faster-whisper'..."`).
Click **Reset** in the Voice panel to recover.

#### Setup: whisper.cpp (alternative)

Use this if you already have a tuned whisper.cpp build or want to
avoid the faster-whisper Python dependency.

1. Build or download a `whisper.cpp` release for Windows (see
   https://github.com/ggerganov/whisper.cpp). You need the CLI
   (`whisper-cli.exe` on recent builds, or `main.exe` on older ones).
2. Download a GGML/GGUF model, e.g. `ggml-base.en.bin`, from the
   whisper.cpp release assets.
3. Install ffmpeg (same as step 1 above).
4. Start the bridge pointing at both:
   ```powershell
   $env:JARVIS_STT_PROVIDER = "whisper.cpp"
   $env:JARVIS_WHISPERCPP_BIN = "C:\tools\whisper.cpp\whisper-cli.exe"
   $env:JARVIS_STT_MODEL = "C:\tools\whisper.cpp\models\ggml-base.en.bin"
   python -m jarvis_core
   ```

#### Adding your own provider

Subclass `TranscriptionProvider` (from `jarvis_core.voice`) and wire
it in — either by extending `build_provider_from_env` or by assigning
directly after `LocalSupervisorAPI` is created:

```python
from jarvis_core.voice import TranscriptionProvider

class MyProvider(TranscriptionProvider):
    name = "my-engine"
    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        ...

api.voice.provider = MyProvider()
```

### TTS (spoken responses)

Short status lines are spoken by the HUD using the browser's
`window.speechSynthesis` (offline on Windows via the built-in SAPI
voices — no network, no extra dependency). Spoken on:

- *"Task accepted."* — after a text or voice task submit
- *"Approval required."* — a new approval appears in the center
- *"Action completed." / "Action blocked." / "Action failed."* — the
  latest action result changes
- *"Approval denied."* — a new `approval.denied` event is traced

TTS is **toggled in the Voice panel** (persisted to `localStorage`) and
is easy to disable: untick *"Speak status"*.

### Running the voice-enabled HUD

With `JARVIS_STT_PROVIDER` unset, the bridge still has **zero extra
dependencies** — it uses the stub provider and the HUD uses the
browser's built-in `MediaRecorder` + `speechSynthesis` APIs. To get
real transcription, follow the faster-whisper or whisper.cpp setup
above (needs `ffmpeg` on PATH + one `pip install`).

1. **Terminal 1** (project root) — start the bridge:

   ```powershell
   python -m jarvis_core
   ```

2. **Terminal 2** — start the Tauri HUD:

   ```powershell
   cd apps\hud
   npm install
   npm run tauri dev
   ```

3. In the HUD's **Voice** panel, tick *"Enable microphone"*. Windows /
   WebView2 will prompt for mic permission on the first PTT press.

4. Hold the *"Hold to talk"* button, say something, release. The
   session transitions recording → transcribing → ready. The stub
   provider returns a labelled placeholder transcript.

5. Edit the transcript if desired, then *Submit as task*. The task
   appears in the Live Task / Trace / Subagents panels exactly as a
   typed task would.

### Privacy and security limitations (read this)

- The **stub provider is not real transcription** — it just echoes
  the payload size. The shipped real providers (`faster-whisper` and
  `whisper.cpp`) run fully offline on the local machine once their
  models are downloaded; no audio leaves the host. Any cloud provider
  you add later must have its privacy properties documented in this
  README *before* it becomes selectable.
- `MediaRecorder` is a browser API. It captures raw audio while the
  PTT button is held; the audio is base64-encoded over `127.0.0.1`
  only, not over the network. It is not persisted to disk by the
  bridge — it is passed straight to the provider and dropped.
- The backend never opens the microphone itself. Only the HUD (user)
  can do that, and only while the PTT button is physically held.
- Voice cannot bypass policy. A transcript that reads *"format disk"*
  goes through the same blocked-pattern check as a typed one.
- There is **no wake word and no continuous listening** in this
  checkpoint. Do not add one without extending these privacy notes.

## End-to-end action loop (HUD-driven)

The HUD now has a first-class **Structured Action** panel and working
Approve/Deny buttons in the Approval Center. Every action still passes
through `ActionGateway` → `PolicyEngine`; the HUD cannot bypass tiers.

**Flow**

```
HUD form  ─propose_action()─▶  bridge POST /actions/propose
                                   │
                                   ├─ Tier 0 / high-conf Tier 1  →  executes immediately
                                   ├─ Tier 2                     →  queues approval
                                   └─ Tier 3 / blocked pattern   →  blocked (never executes)

HUD Approve  ─execute_action()─▶  bridge POST /actions/execute   →  executes with approved=True
HUD Deny     ─deny_action()────▶  bridge POST /actions/deny      →  records approval.denied in trace
```

The HUD's **Latest Action Result** panel shows the status badge,
capability, summary, verification dict, and raw output for the most
recent action (from any task).

### Manual test steps

With the bridge + HUD running (see *Running the full flow* above):

1. **Tier 0 auto-execute** — In the Structured Action panel, pick
   `filesystem.list`, enter path `configs`, click *Propose*. Expect
   `outcome-executed` badge and a list of repo config files in the
   Latest Action Result output.
2. **Tier 1 dry-run** — Pick `filesystem.write`, path
   `runtime/sandbox/hello.txt`, content `hi`, tick *Dry-run*, Propose.
   Expect executed status and `dry_run: true` in output; no file is
   written.
3. **Tier 1 real write** — Same as above without dry-run. Expect the
   file to appear under `runtime/sandbox/`.
4. **Tier 2 approval → execute** — Pick `browser.navigate` with an
   allowlisted URL while setting confidence low enough to force
   conditional approval, *or* use a Tier 2 capability from a REPL
   (`filesystem.move`). Expect an entry in the Approval Center; click
   *Approve & Execute*, and watch the action move to Latest Result with
   `executed` status.
5. **Deny** — Repeat step 4 but click *Deny* with a reason. The
   approval disappears and the task trace shows an `approval.denied`
   event.
6. **Tier 3 block** — Pick `app.launch` with name `regedit` (not on the
   allowlist). Expect `failed` status with a scope error; nothing is
   launched. A `system.delete` proposal with a blocked pattern like
   `format disk` returns a `blocked` decision and never reaches the
   adapter.

## Next recommended step

Use the prompts in `prompts/` to keep implementation staged:

1. ~~Finish the live event/IPC bridge between Python and the HUD.~~ ✅
2. ~~Add real Windows capability adapters behind the existing policy gateway.~~ ✅ (v1; see table above)
3. ~~Wire approval Review/Deny buttons and let the HUD submit typed ActionProposals.~~ ✅ (v1; see *End-to-end action loop*)
4. ~~Add a push-to-talk voice layer with provider abstraction and optional TTS.~~ ✅ (v1; see *Voice layer*)
5. ~~Deterministic planner that converts typed / spoken requests into structured proposals through the existing gateway.~~ ✅ (v1; see *Deterministic planner* below)
6. ~~Browser context awareness: last-read page state + 'read this page' / 'summarize this page' intents, no unsafe automation.~~ ✅ (v1; see *Browser context* below)
7. ~~Bounded multi-step workflow orchestration — finite, inspectable sequences, each step still guarded.~~ ✅ (v1; see *Bounded workflows* below)
8. ~~Windows desktop control (clipboard, notifications, foreground window, real `app.focus`).~~ ✅ (v1; see *Windows desktop commands* below)
9. ~~Local OCR for screenshots and current-window captures (Windows-first, Windows.Media.Ocr).~~ ✅ (v1; see *Local OCR* below)
10. ~~Curated memory & reflection — proposal/approval lifecycle, sensitive-data filter, planner hints.~~ ✅ (v1; see *Memory & reflection* below)
11. Add a real browser-automation channel (CDP or WebView2) for in-page extraction and form interaction.
12. ~~Swap the stub transcription provider for a real local provider (whisper.cpp or faster-whisper) — document privacy properties inline.~~ ✅ (v1; see *Transcription providers*)
13. Integrate wake word behind an explicit, visible privacy mode — not before the verifier/replay harness is in place.
14. Expand the verifier and replay/eval harnesses before increasing autonomy.

## Browser context (v1)

The assistant now carries an **explicit, in-memory** record of what it
has read on the web. The context lives in
`services/orchestrator/src/jarvis_core/browser_context.py` and is shared
by the planner, the HUD, and the browser capability.

### What "browser context" actually contains

A single `BrowserContext` holds the last-read page: `url`, `title`, a
size-capped `textExcerpt`, `byteCount`, `source` (which path wrote it),
and a timestamp. It is populated in **exactly two honest ways**:

1. When the guarded `browser.read_page` or `browser.summarize`
   capability fetches a URL, it records the result here.
2. When the HUD or a local tool **explicitly** pushes a snapshot via
   `POST /browser/snapshot` (the *Browser Context* panel's
   "Set current page" form, or the `browser_snapshot` Tauri command).

It is cleared on restart and when the user clicks **Clear context**.
Nothing in this checkpoint silently scrapes the user's real browser
tabs, runs DOM scripting, auto-clicks, or submits forms.

### New browser capabilities

| Capability             | Tier | Behavior                                                                 |
|------------------------|------|--------------------------------------------------------------------------|
| `browser.read_page`    | 0    | HTTP GET (stdlib, capped at 512 KB). Extracts title + ~4 KB of readable text, updates context. |
| `browser.summarize`    | 0    | Given `url`: fetch + summarize. Given `use_context=true`: reuse last-read. Produces 1–3 deterministic sentences. |
| `browser.current_page` | 0    | Return the stored context (url, title, excerpt). Fails clearly when no context exists. |
| `browser.navigate`     | 0    | Opens URL in the default OS browser (unchanged).                         |
| `browser.download_file`| 2    | Sandbox-gated download (unchanged; still approval-required).             |

All reads are http/https only, size-capped, and strip `<script>` /
`<style>` / tags before storing text. There is no DOM, no JS execution.

### Supported context-aware requests

| Phrasing                                             | Capability            | Notes                                      |
|------------------------------------------------------|-----------------------|--------------------------------------------|
| `read https://example.com`                           | `browser.read_page`   | Populates context as a side effect.        |
| `summarize https://example.com`                      | `browser.summarize`   | Fetches + summarizes.                      |
| `open https://example.com and read it` / `… and summarize it` | `browser.read_page` / `.summarize` | Single fetch; does **not** also open the OS browser. |
| `read this page` / `read the current page`           | `browser.current_page`| **Requires** an existing context.          |
| `summarize this page` / `summarize the current page` | `browser.summarize` (`use_context=true`) | **Requires** an existing context. |
| `what page am I on?` / `which page is open?`         | `browser.current_page`| Pure context read, no network.             |

If the assistant has no browser context yet and you ask it anything
context-relative, the planner returns `clarification_needed` with an
explicit hint ("Ask me to read a URL first"). It never fabricates a
page or guesses which tab you meant.

### What is explicitly **not** supported in this checkpoint

- Reading the user's real browser tabs or history.
- Any DOM access, JavaScript execution, or iframe traversal.
- Auto-clicking links, filling forms, or submitting forms.
- Background crawling, prefetching, or any un-requested fetches.
- Multi-page context (only a single "current page" is tracked).

### HUD surface

A new **Browser Context** panel shows the current URL, title, source,
timestamp, and text excerpt, with a *Clear context* button and a
*Set current page* form for manual snapshots. Browser capability
executions also appear in the trace with their URL and title.

### HTTP bridge endpoints

- `GET  /browser/context` → `{ context: { url, title, textExcerpt, … } | null }`
- `POST /browser/snapshot` with `{ url, title?, text?, byteCount? }` → records an explicit snapshot
- `POST /browser/clear` → clears the in-memory context

### Dev workflow

No new watcher. `python -m jarvis_core.dev_watch` already picks up
edits under `jarvis_core/` (including `browser_context.py` and
`capabilities/browser.py`) and `configs/policy.default.json`. Edit →
save → bridge respawns automatically on the same port.

## Bounded workflows (v1)

The assistant can now decompose a small, explicit set of requests into
a **finite, ordered** sequence of structured actions. Each step is a
normal `ActionProposal` routed through
`SupervisorRuntime.propose_action`, so ActionGateway, PolicyEngine,
approvals, blocked patterns, trace, and the signed audit log all still
apply — nothing new bypasses them.

This is **not** an agent loop. There is no open-ended planning, no
retry budget, no crawling. If a request doesn't match one of the v1
patterns, it falls back to the single-step planner (or stays
`unsupported`). The assistant never improvises a multi-step plan.

### Supported v1 workflow patterns

| Phrasing                                                         | Steps                                                               | Pattern id              |
|------------------------------------------------------------------|---------------------------------------------------------------------|-------------------------|
| `open <url> and read it`                                         | `browser.navigate(url)` → `browser.read_page(url)`                  | `wf.open_and_read`      |
| `open <url> and summarize it`                                    | `browser.navigate(url)` → `browser.summarize(url)`                  | `wf.open_and_summarize` |
| `read <url> then summarize this page`                            | `browser.read_page(url)` → `browser.summarize(use_context=true)`    | `wf.read_then_summarize`|
| `write <text> to runtime/sandbox/<path> then read it back`       | `filesystem.write(path, content)` → `filesystem.read(path)`         | `wf.write_then_read`    |

Write targets **must** be under `runtime/sandbox/`; the planner refuses
anything else (returns `None`, single-step planner then clarifies).

### States

Workflow-level: `queued`, `in_progress`, `waiting_for_approval`,
`blocked`, `completed`, `failed`.

Step-level: `pending`, `running`, `waiting_for_approval`, `completed`,
`failed`, `blocked`, `skipped`.

### Approval + denial semantics

- When a workflow step hits an approval-gated capability (e.g. a Tier 2
  action), the step is marked `waiting_for_approval`, the workflow
  pauses at exactly that step, and the approval is queued normally in
  the *Approval Center*.
- **Approve** → the supervisor executes the paused step, the runner
  records the result, and the workflow continues at the next step.
- **Deny** → the step is marked `failed`, the whole workflow is marked
  `failed` with the denial reason. No further steps run.
- A `blocked`-by-policy step also fails the workflow and halts — a
  later step is never executed in the hope it might succeed.

### HUD surface

A new **Workflow** panel shows the pattern id, current step number,
objective, a step list (capability + intent + live status), any
per-step error, and the final workflow error if it failed. Step cards
highlight the current step and colour-code completed / failed /
waiting states. Workflow lifecycle also appears in the trace as
`workflow.created`, `workflow.in_progress`, `workflow.waiting_for_approval`,
`workflow.completed`, `workflow.failed`.

### Honest limitations

- Only the four phrasings above are recognised. "Do X then Y then Z"
  is not supported — there is no general sequencer.
- No branching, no conditionals, no retries, no loops.
- No cross-task workflow reuse; each task that matches a pattern gets
  its own workflow.
- No step can mutate the workflow dynamically (steps are fully
  materialised up-front from the matched pattern).
- The runner does not introspect capability outputs to decide the next
  step. "Read then summarize" works because
  `browser.summarize(use_context=true)` reads the context that
  `browser.read_page` populated — the sequencing is hard-coded, not
  inferred from results.

### Dev workflow

No new watcher. `python -m jarvis_core.dev_watch` already picks up
edits to `jarvis_core/workflow.py` and `jarvis_core/api.py`. Edit →
save → bridge respawns on the same port.

## Deterministic planner (v1)

`services/orchestrator/src/jarvis_core/planner.py` converts typed or
spoken task text into a structured action proposal that is routed
through the **existing** ActionGateway + PolicyEngine. Nothing bypasses
policy. The planner is **regex-based and LLM-free** — it only recognises
a narrow, explicit intent set and refuses to guess.

### Supported v1 requests

| Phrasing                                             | Capability          | Notes                                    |
|------------------------------------------------------|---------------------|------------------------------------------|
| `open https://example.com` / `go to example.com`     | `browser.navigate`  | Scheme added when missing.               |
| `read https://example.com` / `read the page at …`    | `browser.read_page` |                                          |
| `read configs/policy.default.json` / `read file …`   | `filesystem.read`   | Requires separator or extension.         |
| `list files in configs` / `ls runtime/sandbox`       | `filesystem.list`   |                                          |
| `write hello to runtime/sandbox/hello.txt`           | `filesystem.write`  | Target must be under `runtime/sandbox/`. |
| `open notepad` / `launch calculator` / `open paint`  | `app.launch`        | Allowlist: notepad, calc, calculator, explorer, mspaint. |

Everything else returns `unsupported` or `clarification_needed` — the
HUD's *Auto-Plan* panel shows exactly which rule fired (or didn't),
the extracted parameters, the confidence, and the reason it declined.

### Integration

`LocalSupervisorAPI.submit_voice_or_text_task` runs the planner on every
submission and records the result on the task (`task.context["plan"]` +
a `plan.evaluated` trace event). When the plan maps cleanly it is
forwarded to `SupervisorRuntime.propose_action`, which applies tier
gating, approval queueing, and blocked-pattern refusal exactly as if a
human had proposed the action manually. The plan outcome is stored at
`task.context["planAction"]` and surfaced in the HUD.

### Dev auto-restart for the Python bridge

`python -m jarvis_core.dev_watch` runs the orchestrator under a stdlib
file watcher: it spawns `python -m jarvis_core` as a single child
process, polls the `jarvis_core` package and `configs/` for `.py`
changes, and terminates + respawns the child on edits. Dev-only;
stdlib-only; exactly one child (never duplicate bridges). Forwarded
args (`--port …`, `--root …`) go straight to the child. `dev_watch`
picks up the new `capabilities/desktop.py` module automatically.

## Windows desktop commands (v1)

The assistant now has a small Windows-first desktop layer. It uses
`ctypes` against stable Win32 APIs (`user32` / `kernel32`) — **no new
pip dependencies**, no raw shell, no mouse or keyboard automation.
Every capability still flows through ActionGateway + PolicyEngine and
writes to the signed audit log.

### Supported capabilities

| Capability                 | Tier | Scope                  | What it does                                                        |
|----------------------------|------|------------------------|---------------------------------------------------------------------|
| `desktop.clipboard_read`   | 0    | `desktop.clipboard`    | Reads `CF_UNICODETEXT` from the system clipboard. Truncated at 4 KB.|
| `desktop.clipboard_write`  | 1    | `desktop.clipboard`    | Writes a UTF-16 string to the clipboard. Rejects payloads > 64 KB.  |
| `desktop.notify`           | 1    | `desktop.notify`       | Shows a dialog notification (`MessageBoxW`) from a daemon thread.   |
| `desktop.foreground_window`| 0    | `desktop.window_read`  | Returns the current foreground window's title, PID, and exe path.   |
| `app.focus`                | 1    | `app.launch`           | Raises an **already-running** allowlisted app to the foreground.    |

### Supported phrasings (planner rules)

| You say                                         | Maps to                     |
|-------------------------------------------------|-----------------------------|
| "what is in my clipboard?", "read my clipboard" | `desktop.clipboard_read`    |
| "copy hello world to clipboard"                 | `desktop.clipboard_write`   |
| "send me a notification saying hello"           | `desktop.notify`            |
| "notify me hello jarvis"                        | `desktop.notify`            |
| "show my current window", "what window is open" | `desktop.foreground_window` |
| "focus notepad", "switch to calculator"         | `app.focus`                 |

### New workflow patterns

* `wf.open_and_focus` — "open notepad then focus it"
  → `app.launch` ▸ `app.focus`.
* `wf.copy_and_notify` — "copy hello to clipboard then notify me saying done"
  → `desktop.clipboard_write` ▸ `desktop.notify`.

Both patterns live in `workflow.py` alongside the browser/filesystem
patterns and pause on any approval the same way.

### HUD surface

`DesktopPanel` renders the most recent result for each desktop-flavoured
capability — clipboard read preview (with truncation flag), last clipboard
write byte count, last notification, last foreground-window snapshot, and
last focus attempt (success vs `set_foreground_refused`). Everything is
derived server-side from the supervisor's action results and shipped in
`/hud-state.desktop`.

### How to test each capability

Start the bridge, then either submit a task from the HUD or `curl` the
bridge directly.

```
python -m jarvis_core.dev_watch     # optional — auto-restart on edits
```

* **Clipboard write/read round-trip** (from the HUD task input):
  1. "copy hello world to clipboard"  → `desktop.clipboard_write` runs at tier 1.
  2. "what is in my clipboard?"       → `desktop.clipboard_read` returns the text.
* **Notification**: "notify me hello" — a `MessageBoxW` dialog appears
  in the foreground; the HUD's `Desktop State` panel records it.
* **Foreground window**: "show my current window" — HUD shows title,
  PID, and exe path of whatever is in front.
* **Focus an already-running app**:
  1. "open notepad"                  → launch.
  2. Click somewhere else to defocus it.
  3. "focus notepad"                 → raises it (or reports honestly
     that Windows refused foreground).
* **Bounded workflow**:
  * "open notepad then focus it"    → two-step workflow.
  * "copy hi to clipboard then notify me saying done" → two-step workflow.

### Honest limitations

| Area                 | What you get                               | What is NOT implemented                                      |
|----------------------|--------------------------------------------|--------------------------------------------------------------|
| Platform             | Windows only.                              | macOS and Linux fail with `platform_unsupported`.            |
| Notifications        | Modal-style `MessageBoxW` dialog.          | No Windows toast / action center integration (needs WinRT).  |
| Focus                | `ShowWindow(SW_RESTORE) + SetForegroundWindow`. | No keyboard/mouse simulation. Windows may refuse foreground changes when another app holds the foreground lock; failure is reported as `set_foreground_refused`. |
| Window listing       | Foreground window only.                    | No full window enumeration API yet (`app.focus` enumerates internally but does not expose a list). |
| Clipboard formats    | `CF_UNICODETEXT` (plain text).             | No images, HTML, RTF, or file-drop formats.                  |
| Clipboard size caps  | 4 KB read excerpt, 64 KB write limit.      | Larger payloads are truncated (read) or rejected (write).    |
| App allowlist        | Same as `app.launch` (notepad/calc/paint/explorer). | Third-party apps require explicit allowlist configuration. |
| Shell                | None. No `powershell`, `cmd`, `msg`, or templated shell invocations. | `app.install` remains intentionally unimplemented. |

## Screen & UI awareness (v1)

The assistant can now **capture a screenshot of the foreground window or
the full virtual desktop**, save it to the workspace, and preview it in
the HUD. Capture is user-requested only — there is no background loop,
no periodic snapshotter, and no OCR in v1. Everything flows through
ActionGateway + PolicyEngine and is recorded on the signed event log.

### Supported capabilities

| Capability                       | Tier | Scope                | What it does                                                                  |
|----------------------------------|------|----------------------|-------------------------------------------------------------------------------|
| `desktop.screenshot_foreground`  | 0    | `desktop.screen_read`| Captures the current foreground window via `PrintWindow(PW_RENDERFULLCONTENT)`. |
| `desktop.screenshot_full`        | 0    | `desktop.screen_read`| Captures the virtual screen (all monitors) via `BitBlt(SRCCOPY \| CAPTUREBLT)`. |

PNGs are written to `runtime/screenshots/screenshot-<uuid>.png` using a
stdlib PNG encoder (`struct` + `zlib.crc32` + `zlib.compress`) — no
Pillow dependency.

### Supported phrasings (planner rules)

| You say                                                     | Maps to                           |
|-------------------------------------------------------------|-----------------------------------|
| "take a screenshot", "screenshot", "screenshot my window"   | `desktop.screenshot_foreground`   |
| "take a screenshot of my current window"                    | `desktop.screenshot_foreground`   |
| "what is on my screen?"                                     | `desktop.screenshot_foreground`   |
| "take a full screen screenshot", "capture the entire desktop" | `desktop.screenshot_full`       |
| "screenshot the whole screen", "capture my desktop"         | `desktop.screenshot_full`         |

The planner defaults to the **foreground window** when the phrasing is
ambiguous; the full virtual screen is only used when the user explicitly
says so ("full screen", "entire desktop", "whole screen", "desktop").

### HUD surface

`DesktopPanel` now renders a "Last screenshot" card showing the most
recent capture (either mode). The preview is served by the bridge from
`GET http://127.0.0.1:7821/screenshots/<name>`. The endpoint applies a
strict filename regex (`screenshot-[A-Za-z0-9_-]+\.png`) and verifies
that the resolved path's parent equals the configured screenshots root
before reading, so `..`-style traversal is refused with 404.

### How to test

```
# From the HUD task input:
take a screenshot               # → desktop.screenshot_foreground, PNG on disk, preview in HUD
take a full screen screenshot   # → desktop.screenshot_full across all monitors
```

Or hit the bridge directly:

```powershell
curl -X POST http://127.0.0.1:7821/actions/propose `
     -H 'Content-Type: application/json' `
     -d '{\"capability\":\"desktop.screenshot_foreground\",\"parameters\":{},\"confidence\":0.95}'
```

### Privacy model

* **User-requested only.** No background capture, no wake-on-window,
  no timer. Every PNG on disk traces to an explicit, logged action.
* **Local only.** Screenshots live under `runtime/screenshots/` inside
  the workspace. Nothing is uploaded or sent off-machine.
* **Auditable.** Each capture is a normal action: logged on the signed
  event log, shown in the HUD trace, and listed by capability name.

### Honest limitations

| Area             | What you get                                                       | What is NOT implemented                                      |
|------------------|--------------------------------------------------------------------|--------------------------------------------------------------|
| Platform         | Windows only.                                                      | macOS / Linux fail with `platform_unsupported`.              |
| Foreground mode  | `PrintWindow(hwnd, …, PW_RENDERFULLCONTENT)`.                      | Hardware-accelerated contents (some GPU-composed games, DRM-protected video) may render black — a known Windows limitation. |
| Full-screen mode | `BitBlt` on the desktop DC across the virtual screen rect.         | DRM-protected surfaces render black; we do not attempt to bypass. |
| Format           | 24-bit RGB PNG written by a stdlib encoder.                        | No JPEG, no compression levels, no thumbnailing.             |
| OCR              | Implemented in checkpoint 8 — see *Local OCR (v1)* below.          | Cloud OCR is intentionally not shipped.                      |
| UI tree          | None. No `UIAutomation`, no element inspection, no text-from-DOM.  | Follow-up checkpoint.                                        |
| Size ceiling     | Refuses anything > 8192 px in either dimension.                    | No auto-scaling / downsampling.                              |

## Local OCR (v1)

The assistant can now **extract text from a captured screenshot or the
current foreground window**, fully on-device. Like every other
capability, OCR runs through ActionGateway + PolicyEngine, is recorded
on the signed audit log, and only fires on an explicit user request —
there is no background OCR loop and no periodic polling.

### Supported capabilities

| Capability                  | Tier | Scope                  | What it does                                                                  |
|-----------------------------|------|------------------------|-------------------------------------------------------------------------------|
| `desktop.ocr_foreground`    | 0    | `desktop.screen_read`  | Captures the current foreground window, then runs OCR on the PNG. Saves the source screenshot under `runtime/screenshots/` so the HUD can preview it next to the text. |
| `desktop.ocr_full`          | 0    | `desktop.screen_read`  | Same flow for the full virtual screen.                                        |
| `desktop.ocr_screenshot`    | 0    | `desktop.screen_read`  | Re-runs OCR on a screenshot already on disk. Accepts only canonical `screenshot-<id>.png` filenames; rejects anything else, including path traversal attempts. |

The output is structured and inspectable:

```jsonc
{
  "mode": "foreground",                  // foreground | full | screenshot
  "screenshot": {
    "name": "screenshot-<id>.png",
    "path": "<workspace>/runtime/screenshots/screenshot-<id>.png",
    "width": 1920, "height": 1080,
    "byte_count": 1234567
  },
  "text": "...",                         // extracted text (capped at 64 KB)
  "lines": [{"text": "...", "confidence": null}, ...],   // capped at 5,000
  "truncated": false,                    // true if text or line cap kicked in
  "byte_count": 1234,                    // pre-truncation utf-8 byte length
  "char_count": 1234,
  "line_count": 42,                      // honest count even if `lines` was clipped
  "average_confidence": null,            // null when provider doesn't expose it
  "language": "en-US",
  "provider": "windows-media-ocr",       // honest identifier — see provider table
  "dry_run": false
}
```

### Supported phrasings (planner rules)

| You say                                                          | Maps to                                              |
|------------------------------------------------------------------|------------------------------------------------------|
| `ocr my window` / `ocr my current window` / `ocr foreground window` | `desktop.ocr_foreground`                          |
| `ocr my screen` (default — explicitly foreground)                | `desktop.ocr_foreground`                             |
| `read text from my current window`                               | `desktop.ocr_foreground`                             |
| `extract text from my screen` / `extract text from my window`    | `desktop.ocr_foreground`                             |
| `what text is on my screen?` / `what text is in this window?`    | `desktop.ocr_foreground`                             |
| `take a screenshot and read it` / `screenshot and ocr it`        | `desktop.ocr_foreground` (single capability captures + OCRs in one step) |
| `ocr full screen` / `ocr the entire desktop` / `ocr the whole screen` | `desktop.ocr_full`                              |
| `what text is on my full screen?`                                | `desktop.ocr_full`                                   |
| `take a full screen screenshot and read it`                      | `desktop.ocr_full`                                   |
| `ocr screenshot-<id>.png`                                        | `desktop.ocr_screenshot` (with strict name validation) |

The OCR rule is checked **before** the screenshot rule, so
`what text is on my screen` correctly maps to OCR while
`what is on my screen` (no "text") still maps to a plain screenshot.

### Setup

Default behaviour ships with the **`unavailable`** OCR provider — every
OCR action returns a `failed` ActionResult with a remediation hint. No
fake or placeholder text is ever generated. To enable real OCR:

1. **Install winsdk:**
   ```powershell
   python -m pip install winsdk
   ```
2. **Install at least one Windows OCR language pack** (free, on-device):
   * Settings → Time & language → Language → *Add a language*
   * Pick the language you want OCR for (e.g. *English (United States)*)
   * Click *Next* → enable **Optical character recognition** under
     *Optional language features* → *Install*
3. **Start the bridge with OCR enabled:**
   ```powershell
   $env:JARVIS_OCR_PROVIDER = "windows-media-ocr"
   # Optional: hint a specific language tag.
   $env:JARVIS_OCR_LANGUAGE = "en-US"
   python -m jarvis_core
   ```
   Or use `auto` to fall back gracefully if `winsdk` isn't installed:
   ```powershell
   $env:JARVIS_OCR_PROVIDER = "auto"
   ```

| Variable                | Values                                                       |
|-------------------------|--------------------------------------------------------------|
| `JARVIS_OCR_PROVIDER`   | `unavailable` (default), `windows-media-ocr`, `auto`         |
| `JARVIS_OCR_LANGUAGE`   | BCP-47 tag, e.g. `en-US`, `fr-FR`. Defaults to the first language installed in the user profile. |

`auto` reports the actual chain it ran (e.g. `windows-media-ocr+unavailable`) in the
HUD's *Last OCR result* card so you can tell whether you're getting real
OCR or hitting the unavailable fallback.

### How to test end-to-end

With the bridge + HUD running and `JARVIS_OCR_PROVIDER=windows-media-ocr`:

1. **Foreground OCR** — Type `ocr my current window` (or hold push-to-talk
   and say it) into the HUD task box. The Auto-Plan panel should show
   `desktop.ocr_foreground`. The Desktop State panel renders a *Last OCR
   result* card with the source screenshot, line count, and the extracted
   text in a focusable `<pre>` block.
2. **Full screen OCR** — `ocr the entire desktop`. Expect the *Last OCR
   result* card to use `mode: full` and the screenshot to span all monitors.
3. **Re-OCR an existing screenshot** — first run `take a screenshot`,
   note the `screenshot-…png` name shown under *Last screenshot*, then
   say `ocr screenshot-<that-id>.png`. The OCR rule pulls the file from
   `runtime/screenshots/` (path-traversal hardened) and runs the
   provider on it.
4. **No provider configured** — without setting
   `JARVIS_OCR_PROVIDER`, the same request returns `failed` with a clear
   remediation hint pointing at this README.
5. **Bridge endpoint** — `GET http://127.0.0.1:7821/hud-state` includes
   `desktop.latestOcr` with text, lines, provider, language, screenshot
   metadata, and a truncation flag. The source PNG is served by the
   existing `GET /screenshots/<name>` endpoint.

Or hit the bridge directly:

```powershell
curl -X POST http://127.0.0.1:7821/actions/propose `
     -H 'Content-Type: application/json' `
     -d '{\"capability\":\"desktop.ocr_foreground\",\"parameters\":{},\"confidence\":0.95}'
```

### Privacy model

* **User-requested only.** OCR runs only when an explicit action arrives
  through the gateway. There is no background loop, no periodic timer,
  no wake-on-window. Every OCR result has a corresponding entry on the
  signed audit log.
* **Local only.** `windows-media-ocr` uses Microsoft's on-device OCR
  engine (the same one File Explorer uses for image search). No image
  bytes leave the machine. Cloud OCR is intentionally not shipped — if
  you add a cloud provider later, document the privacy impact in this
  section *before* it becomes selectable.
* **No mouse / keyboard automation.** OCR is read-only. This checkpoint
  introduces zero new write surface.
* **No DOM / no UIAutomation.** OCR works on raster pixels, not on the
  underlying app's accessibility tree. App-aware text extraction is a
  separate (deferred) capability.

### Honest limitations

| Area               | What you get                                                                                | What is NOT implemented                                                                            |
|--------------------|---------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| Platform           | Windows only.                                                                               | macOS / Linux fail with `platform_unsupported`.                                                    |
| Provider           | `unavailable` (default) and `windows-media-ocr` (via the `winsdk` pip package).             | No Tesseract, no EasyOCR, no cloud APIs. The provider abstraction makes adding one straightforward. |
| Confidence         | Windows.Media.Ocr does NOT surface per-word or per-line confidence; we report `null` in `confidence` and `average_confidence`. | Not a bug — the underlying API genuinely does not expose it. Other providers may fill it in. |
| Languages          | Whatever Windows OCR language packs are installed in the user profile.                      | We do not download language packs. Without one, the provider raises a clear error.                 |
| GPU-composed surfaces | OCR runs on the captured PNG. If the screenshot itself rendered black (DRM, hardware overlay), OCR will see nothing. | We don't try to bypass DRM.                                                                    |
| Truncation         | Text capped at 64 KB; line list capped at 5,000. Both flag `truncated: true` honestly; full counts are still reported in `byte_count` / `line_count`. | No streaming. The full text is in the action result `output` even after the HUD-facing copy is clipped. |
| `desktop.ocr_screenshot` | Filename must match `screenshot-<id>.png` exactly (regex + parent-resolve guard). | No way to OCR an arbitrary file outside `runtime/screenshots/`. By design.                  |
| UI tree            | None. OCR is pixel-based, not semantic.                                                     | UIAutomation / accessibility tree extraction is a separate (deferred) capability.                   |
| Workflow           | The capture+OCR composite lives inside a single capability call — no workflow needed.       | We did NOT add a `wf.screenshot_then_ocr` workflow. The runner is fully-materialised up-front and does not introspect step outputs to decide later steps; staying within one capability preserves that contract. |

### Dev workflow

`python -m jarvis_core.dev_watch` already picks up edits under
`jarvis_core/` (including the new `ocr_providers.py` and changes to
`capabilities/desktop.py`, `planner.py`, and `bridge.py`) plus
`configs/policy.default.json`. Edit → save → bridge respawns
automatically on the same port.

## Memory & reflection (v1)

The assistant now has a **curated** memory layer. Proposals come from a
deterministic post-task reflection pass; approvals come from the user;
and approved memory is allowed to *annotate* a plan but never to change
the chosen capability, parameters, or confidence. Policy stays
authoritative.

### Layers

`runtime/memory/` holds one JSON file per layer:

| Layer         | What it captures                                                            | Example                                                               |
|---------------|-----------------------------------------------------------------------------|-----------------------------------------------------------------------|
| `profile`     | Explicit user preferences mined from the objective only                     | "User preference: https when no scheme is given"                      |
| `operational` | Runtime notes that help future planning (e.g. clean workflow runs)          | "Workflow wf.open_and_focus completed cleanly in 2 steps."            |
| `lesson`      | Cause-effect lessons from planner clarifications                            | "Planner clarification (write.outside_sandbox): target must be under runtime/sandbox/" |
| `tool`        | Tool reliability notes from failed or blocked actions                       | "filesystem.write failed with error_type=ScopeError."                 |

Each row carries: `kind`, `summary`, `details` (only structured
metadata — no user text), `evidence` (task + capability ids), `trust_score`,
`status`, `reviewed_at`, `reviewed_by`, `review_reason`, `created_at`,
`memory_id`.

### Lifecycle

```
candidate ──approve()──▶ approved ──expire()──▶ expired
candidate ──reject(reason)──▶ rejected
```

`reject` and `expire` keep the row for audit (a rejected lesson tells
us what NOT to repeat) and are reversible by editing the JSON or by
re-proposing. `delete` is a separate physical removal — used sparingly.

Every transition is recorded on the signed event log (`memory.approved`,
`memory.rejected`, `memory.expired`), and the HUD shows the current
status alongside `reviewed_at` / `reviewed_by` / `review_reason` for
each row.

### Reflection (what proposes a memory)

`services/orchestrator/src/jarvis_core/reflection.py` runs at the end
of every action and proposes memory ONLY for these patterns:

* **Tool reliability** — an action failed or was blocked by policy.
  Records the capability + `error_type` only — never the error body or
  the parameters that triggered it.
* **Planner clarification** — `plan.evaluated` came back
  `clarification_needed`. Records the `matched_rule` and an excerpt of
  the *user's* objective (capped at 200 chars).
* **Workflow completed** — a `wf.*` finished cleanly. Records the
  `pattern_id` and the list of step capabilities (no parameters).
* **Explicit preference** — the *objective itself* matches one of:
  `I prefer …`, `I'd prefer …`, `always …`, `never …`, `from now on …`,
  `please always …`. Records the captured preference phrase.

The Reflector dedupes within a task (same `(kind, dedup_key)` is filed
at most once), so a long task with repeated failures of the same
capability produces a single tool note — not a stream.

### Sensitive-payload filter

`MemoryStore.propose` runs every item through
`reflection.is_sensitive_payload` before persisting. The filter rejects:

* Summary text containing clipboard/OCR/transcript/screenshot phrases
  (e.g. "Clipboard contains: …", "OCR result: …", "Transcript was: …").
* `details[*]` carrying user text under any of the keys: `text`,
  `transcript`, `excerpt`, `raw_text`, `raw_audio`, `audio`,
  `audio_base64`, `ocr_text`, `clipboard`, `screenshot`,
  `screenshot_bytes`, `png_bytes`, `content`.
* Evidence lists that mention any of the same phrases.
* Free-form summaries longer than 800 chars.

Sensitive-preference objectives are also skipped: `always store the
clipboard contents` will NOT become a profile memory because
`clipboard` is one of the sensitive-data verbs (`clipboard`, `ocr`,
`screenshot`, `transcript`, `extract`, `read text`, `voice`,
`microphone`, `password`, `secret`, `token`, `credential`).

The filter is enforced inside the `MemoryStore.propose` call, so even
hand-written or future code paths cannot smuggle user content into
long-term memory.

### Memory as planning hints (the safe path)

The `DeterministicPlanner` accepts an `ApprovedMemoryHints` view that
returns approved memories matching the chosen capability or rule.
Hints land on `PlanResult.memoryHints` and are surfaced in the HUD as
a live-region badge ("Memory influenced this plan: …"). Memory hints
are **advisory only**:

* They never change `capability`, `parameters`, or `confidence`.
* They never alter the policy decision. A Tier 2 capability stays
  Tier 2 even when an approved profile memory says otherwise.
* They never run new actions on their own.

This is verified by `test_memory.MemoryDoesNotBypassPolicyTests`.

### HUD controls

A new **Memory** panel groups items into:

* **Pending proposals** — every candidate. Each row has Approve and
  Reject buttons; rejection prompts for an optional reason.
* **Approved** — what the planner can use as a hint. Each row has an
  Expire button.
* **Recent** — collapsible list of rejected/expired rows with the
  reason, kept for audit.

A polite `aria-live` toast announces the result of every approve /
reject / expire action. Disabled buttons during in-flight requests use
`aria-disabled` so focus is preserved.

### Bridge endpoints

| Method | Path                              | Purpose                                  |
|-------:|-----------------------------------|------------------------------------------|
|  GET   | `/memory?status=&kind=`           | Filtered memory list (existing, extended) |
|  GET   | `/memory/proposals`               | Convenience: `status=candidate`          |
|  POST  | `/memory/{memory_id}/approve`     | Flip status to `approved`                |
|  POST  | `/memory/{memory_id}/reject`      | Body `{reason}` — flip to `rejected`     |
|  POST  | `/memory/{memory_id}/expire`      | Flip status to `expired`                 |

All four are wrapped by Tauri commands `memory_approve`,
`memory_reject`, `memory_expire`, `memory_proposals`.

### How to test memory end-to-end

1. Run the unit tests:
   ```powershell
   python -m unittest discover -s services/orchestrator/tests -t services/orchestrator
   ```
   Memory + reflection tests live in `tests/test_memory.py`; the existing
   runtime tests now assert the new reflection contract.

2. With the bridge + HUD running, make a tool fail (e.g. propose a
   `filesystem.read` for an absolute path outside the workspace):
   ```powershell
   curl -X POST http://127.0.0.1:7821/actions/propose `
        -H "Content-Type: application/json" `
        -d '{\"capability\":\"filesystem.read\",\"parameters\":{\"path\":\"C:/Windows/notepad.exe\"},\"confidence\":0.9}'
   ```
   The Memory panel should show one new pending `tool` proposal:
   "filesystem.read failed with error_type=ScopeError."

3. Click **Approve** in the Memory panel. The toast announces "Memory
   approved." and the row moves to the Approved section.

4. Submit any new task; the Auto-Plan panel's *Memory influenced this
   plan* live region appears when the planner picks a capability that
   matches the approved memory.

5. **Privacy verification.** Try to file a memory carrying sensitive
   content directly:
   ```powershell
   python -c "
   from src.jarvis_core.api import LocalSupervisorAPI
   from src.jarvis_core.models import MemoryItem
   from pathlib import Path
   api = LocalSupervisorAPI(Path('.'))
   try:
       api.memory.propose(MemoryItem(kind='lesson', summary='ok',
           details={'text': 'raw clipboard contents'},
           evidence=[], trust_score=0.5))
   except Exception as e:
       print('refused:', e)
   "
   ```
   Expect `refused: Refusing to store memory of kind 'lesson': details['text'] carries user content; …`.

### What memory CAN influence

* The HUD's *Memory influenced this plan* badge — purely informational.
* Future versions of the planner could read approved memory to
  re-rank ambiguous interpretations. v1 only surfaces hints; it does
  not re-rank.

### What memory CANNOT influence

* The chosen capability for any planner rule.
* The planned parameters.
* The confidence score.
* The policy tier or any approval requirement. Tier 2 stays Tier 2
  regardless of approved memory.
* The blocked-pattern list.
* Whether an action runs at all.

### Privacy & honest limitations

* **Local only.** All memory lives in `runtime/memory/*.json` on disk.
  Nothing is uploaded.
* **No raw user content.** Clipboard text, OCR output, screenshot
  bytes, raw audio, raw transcripts, and `filesystem.write` body
  content are blocked at the store level. The Reflector only ever
  proposes structural metadata (capability, error_type, matched_rule,
  pattern_id, step capability list) plus a length-bounded excerpt of
  the user's typed objective when an explicit preference phrase
  matches.
* **Approval is mandatory.** Candidates do not influence anything
  until a human approves them through the HUD.
* **Reversibility.** Approve, reject, and expire are all reversible
  by editing `runtime/memory/*.json` or re-proposing. The signed event
  log records every transition.
* **No auto-promotion.** The Reflector never approves its own
  proposals. There is no daemon that promotes memories without a
  user click.
* **No hidden memory.** Every layer is a plain JSON file under
  `runtime/memory/` — `cat profile.json operational.json lesson.json
  tool.json` is the entire long-term store.
* **No semantic recall yet.** Memory match is exact-structured
  (capability == capability, matched_rule == matched_rule). There is
  no embedding store and no fuzzy retrieval. v2 might add it; v1 is
  deliberately boring.
* **Single-user assumption.** No tenant isolation, no per-user
  partitioning. Approve/reject are attributed to a literal `"user"`
  string by default.

