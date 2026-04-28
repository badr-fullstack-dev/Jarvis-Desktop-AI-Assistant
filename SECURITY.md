# Security Policy

Thank you for helping keep this project safe.

This assistant is designed to run on a personal Windows PC, interact with local files, browser context, voice input, screenshots, OCR, and guarded desktop actions. That makes security reports especially important. If you find something that could let the assistant bypass approvals, leak private data, execute unsafe actions, or weaken the audit trail, please report it privately first.

## Supported Versions

This project is still early-stage. For now, security fixes are handled on the `main` branch only.

| Version | Supported |
| --- | --- |
| `main` | Yes |
| older commits / forks | No |

## How To Report A Vulnerability

Please use GitHub's private vulnerability reporting feature for this repository if it is enabled.

If private reporting is not available, open a public issue with only a short, non-sensitive note such as:

> I found a possible security issue. Please enable private vulnerability reporting or provide a safe contact path.

Please do not post exploit details, screenshots containing private data, tokens, local file paths, transcripts, OCR output, clipboard contents, or logs publicly.

## What To Report Privately

Please report anything that could affect user safety or local machine security, including:

- A way to bypass `PolicyEngine`, `ActionGateway`, risk tiers, or approval requirements.
- A way for voice, OCR, browser content, memory, or replay data to execute actions without the same policy checks as typed input.
- Prompt-injection behavior that causes unsafe tool use, approval bypass, memory poisoning, or secret exposure.
- Secrets, credentials, tokens, local paths, screenshots, audio, transcripts, OCR text, clipboard data, or file contents being stored or displayed where they should be redacted.
- A bug that lets filesystem operations escape their configured root or sandbox.
- A bug that lets browser downloads, app launches, installs, or desktop actions run outside their allowlists or scopes.
- Audit-log tampering that is not detected, or signed event logs that can be forged because of weak/default key handling.
- Unsafe behavior after restart, such as restoring old approvals as executable or auto-resuming actions without fresh user confirmation.

## Safe Public Issues

It is fine to open normal public issues for:

- Build failures.
- UI bugs.
- Documentation mistakes.
- Feature requests.
- Test failures that do not reveal private data.
- Performance problems that do not include sensitive logs.

When in doubt, keep the first report private. We can always move safe details into a public issue later.

## What To Include

For private reports, please include as much of this as you safely can:

- A short description of the problem.
- The affected component, if known, such as `voice`, `desktop`, `memory`, `browser`, `policy`, `gateway`, `replay`, or `audit log`.
- Steps to reproduce using dummy data.
- The expected safe behavior.
- The actual unsafe behavior.
- The commit hash or branch tested.
- Any relevant logs after removing private data.

Please avoid sending real secrets, personal files, real screenshots, real transcripts, or real clipboard contents. Minimal synthetic examples are much safer and usually enough.

## Project Security Principles

This project should stay boring where safety matters:

- Local-first by default.
- No hidden background recording.
- No raw unrestricted shell access from model output.
- No action bypassing the central `ActionGateway`.
- Human approval for sensitive actions.
- Redaction before memory, replay, or diagnostics persist user content.
- Signed append-only audit events.
- No autonomous self-modification of core code, policies, or approval rules.
- No auto-resume of interrupted approvals or actions after restart.

## Disclosure Expectations

Please give maintainers reasonable time to investigate and fix security issues before public disclosure. We will try to acknowledge valid private reports promptly and keep the reporter updated as the fix progresses.

This is a personal-assistant project, so protecting user trust matters more than looking clever. If a report shows that the system is doing something risky, the right fix is to make the risk visible, gated, testable, and documented.
