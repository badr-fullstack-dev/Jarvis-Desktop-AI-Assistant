# Claude Prompt: Foundation Hardening

You are implementing the next checkpoint of a Windows-first guarded desktop assistant in this repository.

Goals:
- Keep the existing policy-gated architecture intact.
- Do not bypass the action gateway or approval rules.
- Replace remaining mock/demo state with live data flows where possible.

Deliverables:
- Strengthen cross-language schemas so Python, Rust, and HUD share the same event and action contracts.
- Add persistence and replay tooling around the signed audit log.
- Improve unit test coverage for policy enforcement, blocked actions, and malformed events.
- Document any assumption or temporary stub that still prevents production use.

Constraints:
- Preserve guarded autonomy defaults.
- Do not add autonomous self-modifying behavior.
- Prefer small, testable increments.

