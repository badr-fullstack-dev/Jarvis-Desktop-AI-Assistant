# Claude Prompt: Windows Capability Adapters

Implement the next capability slice for this assistant.

Goals:
- Add real Windows adapters for browser automation, filesystem operations, and application launch/focus.
- Keep every operation behind typed capability interfaces, policy scopes, and approval gating.

Deliverables:
- Replace stubs with real adapters where safe.
- Add dry-run support and verification hooks for each adapter.
- Ensure Tier 2 and Tier 3 actions remain approval-gated or blocked.
- Add tests and clear failure handling for unavailable apps, invalid paths, and scope violations.

Constraints:
- No raw unrestricted shell execution from model outputs.
- Prefer official APIs or stable automation layers over brittle click scripting.
- Maintain audit logs and verification traces for every executed action.

