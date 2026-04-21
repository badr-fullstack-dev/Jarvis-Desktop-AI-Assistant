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
- A prompt pack for Claude to keep implementation staged and safe.

## Current limitations

- Rust tooling is not available in the current sandbox, so the Rust/Tauri pieces are scaffolded but not compiled here.
- Voice, wake word, and STT/TTS are not wired to external services yet.
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
C:\Users\badre\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m jarvis_core
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
- `prompts/claude`: implementation prompts for Claude by build phase

## Running the Python tests

Use the bundled Python runtime from the Codex desktop app:

```powershell
C:\Users\badre\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest discover -s services/orchestrator/tests -t services/orchestrator
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
| `app.launch` / `app.focus` | 1 | Allowlisted launch (notepad, calc, explorer, mspaint) via `subprocess.Popen`. No arbitrary paths. |
| `app.install`           | 2   | Still intentionally unsupported — returns `failed: not_implemented` after approval. |

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
C:\Users\badre\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -i -c "
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
C:\Users\badre\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest discover -s services/orchestrator/tests -t services/orchestrator
```

This runs runtime + bridge + capability + action-loop + voice tests
(57 tests total in this checkpoint). The capability tests use a local
loopback HTTPServer for browser tests and dry-run mode for
`app.launch`, so no external network or GUI processes are started.
Voice tests inject deterministic providers — no microphone is opened.

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

### Transcription provider

`services/orchestrator/src/jarvis_core/voice.py` defines a
`TranscriptionProvider` ABC. The default, shipped implementation is
`StubTranscriptionProvider`. **It is NOT real speech recognition** —
it returns a clearly-labelled synthetic string that includes the
audio size and MIME type. This exists so the end-to-end plumbing
(mic → backend → provider → transcript preview → task) can be
exercised honestly before a real provider is wired in.

No third-party dependency is installed by default. To plug in a real
provider, subclass `TranscriptionProvider` and assign it on the API:

```python
from jarvis_core.voice import TranscriptionProvider

class MyWhisperProvider(TranscriptionProvider):
    name = "whisper.cpp-local"
    def transcribe(self, audio_bytes: bytes, mime: str) -> str:
        ...  # call whisper.cpp / sapi / isolated cloud endpoint

api.voice.provider = MyWhisperProvider()
```

Possible real implementations (none are shipped in this checkpoint):

- **whisper.cpp** — local, offline, Windows-friendly. Pipe `audio_bytes`
  to `main.exe -otxt`. Privacy: never leaves the machine.
- **Windows SAPI dictation** (`System.Speech.Recognition`) via a small
  PowerShell bridge. Privacy: never leaves the machine, but quality is
  modest.
- **Isolated cloud API** (OpenAI Whisper API, Azure Speech). Privacy:
  audio leaves the machine — document this prominently before enabling.

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

No extra dependencies vs. the previous checkpoint — the bridge is
still stdlib-only and the HUD uses the browser's built-in
`MediaRecorder` + `speechSynthesis` APIs.

1. **Terminal 1** (project root) — start the bridge:

   ```powershell
   C:\Users\badre\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m jarvis_core
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
  the payload size. Any real provider you add must have its privacy
  properties documented in this README, and its audio path reviewed.
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

Use the prompts in `prompts/claude` to keep implementation staged:

1. ~~Finish the live event/IPC bridge between Python and the HUD.~~ ✅
2. ~~Add real Windows capability adapters behind the existing policy gateway.~~ ✅ (v1; see table above)
3. ~~Wire approval Review/Deny buttons and let the HUD submit typed ActionProposals.~~ ✅ (v1; see *End-to-end action loop*)
4. ~~Add a push-to-talk voice layer with provider abstraction and optional TTS.~~ ✅ (v1; see *Voice layer*)
5. Add a real browser-automation channel (CDP or WebView2) for in-page extraction and form interaction.
6. Add `app.focus` via UIA so focus works on already-running processes.
7. Swap the stub transcription provider for a real local provider (whisper.cpp or SAPI dictation) — document privacy properties inline.
8. Integrate wake word behind an explicit, visible privacy mode — not before the verifier/replay harness is in place.
9. Expand the verifier and replay/eval harnesses before increasing autonomy.

