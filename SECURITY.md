# Security Policy

Jarvis is an **experimental, locally-running, guarded-autonomy desktop
assistant**. It is not a fully trusted autonomous operator. This file
covers how to report vulnerabilities and what the project's security
posture actually is so you can judge it for yourself.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a suspected
vulnerability.

Instead, use GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Include:
   - Affected file(s) and version (commit SHA).
   - Reproduction steps. Use synthetic data only — no real screenshots,
     audit logs, OCR text, microphone audio, or memory contents.
   - Impact assessment (read-only? capability bypass? policy bypass?).

I will acknowledge reports as quickly as I can and aim to fix
high-severity issues before any public disclosure.

## Threat model (what this project does and does not protect against)

**In scope.** The repository is designed to defend against:

- Accidental escalation: every action passes through `ActionGateway`
  and `PolicyEngine`. Tier-2 actions require explicit approval.
- Silent action: `SignedEventLog` chain-signs `runtime/events.jsonl`
  with a per-install HMAC key (`runtime/audit.key`, git-ignored).
- Sensitive payload leakage to memory: `reflection.is_sensitive_payload`
  scrubs the canonical user-content keys produced by the existing
  capability adapters before anything is stored.
- Mic/screen privacy: the microphone is **push-to-talk only** and only
  the HUD records. OCR/STT default to clearly-labelled stubs; nothing
  real happens without an explicit env-var opt-in.

**Out of scope.** This project does **not** protect against:

- A malicious operator on the same machine. Anything `runtime/`
  contains is local data and is no more protected than the user's
  filesystem.
- Tamper *recovery*. A failed `verify_chain` is surfaced loudly but
  the assistant will not auto-repair the audit log — repairing a
  signed log automatically would destroy its evidentiary value.
- Cloud / third-party providers. The assistant does not phone home,
  but if you opt in to a non-stub STT/OCR backend that itself sends
  data anywhere, that is between you and that backend.
- Supply chain. Pin and review your own dependencies.

## Known Dependabot alerts and accepted scope

Some Dependabot alerts cannot be fixed at the leaf-package level
because their patched versions are pinned upstream by Tauri 2.10.x.
Where the affected code path is provably outside the Windows-first
runtime surface, the alert is documented here rather than force-bumped.

- **`glib` `<0.20.0` (unsoundness in `VariantStrIter`).** Pulled in
  exclusively via the GTK / WebKitGTK path (`gtk → atk → glib`),
  which is Linux-only. The Windows-first build uses WebView2-COM and
  never links GTK or `glib`. Will be picked up automatically when
  Tauri publishes a release using `gtk@0.21+`.
- **`rand` `<0.8.6` (unsoundness with custom logger).** Pulled in
  exclusively as a build-time dependency through
  `phf_codegen → selectors → kuchikiki → tauri-utils`. It generates
  CSS-selector lookup tables at compile time; runtime never executes
  this code, and the affected `rand::rng()` path is not used by any
  build script we ship. Will be picked up automatically when
  `tauri-utils` updates `phf` past 0.10.

These are revisited on every Tauri release.

## Static analysis

CodeQL static analysis runs through GitHub's repository-level
**default setup** (Settings → Code security → Code scanning). That
covers JavaScript/TypeScript and Python automatically. Rust is not a
CodeQL-supported language at the time of writing, so the Tauri shell
is exercised by `cargo check --locked` in `.github/workflows/ci.yml`.

A custom CodeQL workflow file is intentionally **not** committed —
GitHub rejects advanced-config SARIF uploads while default setup is
enabled, and default setup is sufficient for this project. To switch
to advanced configuration later, first disable default setup in
repository settings, then add a workflow under `.github/workflows/`.

## Secrets and local data

- The audit signing key lives at `runtime/audit.key` and is generated
  with `secrets.token_hex(32)` on first run. `runtime/` is git-ignored.
- No real API keys, OAuth tokens, or third-party credentials are
  required to run the default configuration. If you wire one in via
  env vars, keep it in a local `.env` (also git-ignored) — never commit
  it.
- `runtime/`, screenshots, OCR text, microphone debug dumps, memory
  files, and event logs are **private local data**. Do not paste any
  of them into public issues, PRs, or discussions.

## Responsible-disclosure expectations

- I appreciate reproducible reports with synthetic data.
- I will credit reporters who want credit, and respect requests to
  remain anonymous.
- This is a personal/experimental project — please be patient with
  response times.
