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
- The HUD currently uses shared demo state instead of a live IPC bridge.
- Voice, wake word, STT/TTS, and real browser/device automation are not wired to external services in this checkpoint.
- Capabilities are intentionally conservative stubs; destructive actions remain blocked by policy.

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

## Next recommended step

Use the prompts in `prompts/claude` to keep implementation staged:

1. Finish the live event/IPC bridge between Python and the HUD.
2. Add real Windows capability adapters behind the existing policy gateway.
3. Integrate wake word and transcription providers behind explicit privacy modes.
4. Expand the verifier and replay/eval harnesses before increasing autonomy.

