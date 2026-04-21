# Architecture Notes

## Design intent

This repository treats the assistant as a supervised automation platform, not as a raw all-powerful chatbot. The model layer can propose plans and actions, but every concrete operation flows through typed capability adapters, policy checks, approval rules, audit logging, and verification.

## Runtime overview

1. Voice or text enters the supervisor.
2. The supervisor opens a task session and writes a `task.created` event.
3. Subagents read the shared blackboard and produce structured outputs.
4. The action gateway receives proposals, scores risk from policy, and either:
   - auto-executes safe actions,
   - raises an approval request,
   - blocks the action.
5. The verifier checks outcomes and records traces.
6. The memory curator proposes lessons and preferences with evidence links.
7. The HUD displays the live plan, approvals, memory candidates, and trace replay.

## Security principles

- Least privilege by capability scope
- No unrestricted shell tool for the model
- Signed append-only audit trail
- Explicit approvals for sensitive actions
- Blocked destructive operations by default
- Separated memory layers with review metadata
- Sandboxed capability development before promotion

## Checkpoint strategy

This first checkpoint is meant to be a safe platform for incremental expansion:

- Python owns orchestration and testability.
- Rust owns security-sensitive native integrations later.
- Tauri/React owns the human-facing cockpit and approval center.
- Shared schemas keep cross-language contracts stable.

