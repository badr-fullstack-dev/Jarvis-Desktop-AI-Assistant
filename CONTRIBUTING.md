# Contributing

Thanks for your interest. This is an experimental guarded-autonomy
desktop assistant, so contributions need to keep the security model
intact. The rules below are short on purpose and non-negotiable.

## Ground rules

1. **No secrets.** Never commit API keys, OAuth tokens, audit signing
   keys, `.env` files, `runtime/audit.key`, or any other credential.
   If you find a leaked secret in history, report it privately via the
   process in `SECURITY.md` — do **not** open a public issue.

2. **No raw user data in tests.** Tests must use synthetic fixtures.
   Do not commit real screenshots, OCR text, microphone audio, audit
   logs, memory JSON, or anything else generated under `runtime/`.
   `.gitignore` is your friend; if you're tempted to use `git add -f`
   to bypass it, stop.

3. **No capability bypasses.** Every action must pass through
   `ActionGateway` and `PolicyEngine`. Don't add code paths that:
   - call adapters directly,
   - skip approval for Tier-2 actions,
   - downgrade a capability's tier,
   - introduce raw shell execution,
   - or write to the audit log without going through `SignedEventLog`.

   If you genuinely need a new capability, add a new typed adapter
   and a new policy entry — don't shortcut the gate.

4. **Tests required for security-sensitive changes.** Anything that
   touches `policy.py`, `gateway.py`, `event_log.py`, `reflection.py`,
   `api.py`'s audit-secret loader, or capability adapters must come
   with unit tests proving the security property still holds. PRs that
   weaken a guarantee without a test demonstrating the new behaviour
   will be sent back.

## Local development

- Python tests:
  ```powershell
  python -m unittest discover -s services/orchestrator/tests -t services/orchestrator
  ```
- HUD build:
  ```powershell
  npm install
  npm --workspace apps/hud run build
  ```
- Tauri shell check:
  ```powershell
  cargo check --manifest-path apps/hud/src-tauri/Cargo.toml --locked
  ```

CI runs these on `windows-latest` for every PR and push to `main`.

## PR checklist

Before requesting review, confirm:

- [ ] No new files under `runtime/`, `dist/`, `target/`, or anything
      else listed in `.gitignore` are being committed.
- [ ] No real `.env` is staged. Updates belong in `.env.example`.
- [ ] `python -m unittest discover -s services/orchestrator/tests -t services/orchestrator`
      passes locally.
- [ ] If the change touches the security-sensitive modules listed
      above, you've added or updated tests.
- [ ] No public-issue snippets contain real screenshots, OCR text,
      audio, audit logs, or memory JSON.

## Reporting bugs vs. vulnerabilities

- **Bugs**: open a normal GitHub issue. Use synthetic repro data only.
- **Security vulnerabilities**: follow `SECURITY.md`. Do not file a
  public issue.
