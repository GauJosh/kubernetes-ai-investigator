# Security Policy

## Supported versions

This project is pre-1.0 and does not yet maintain multiple supported release branches. Security
fixes are made against `main`.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a suspected security vulnerability.

Instead, use GitHub's private vulnerability reporting for this repository: go to the
**Security** tab → **Report a vulnerability**. This opens a private advisory visible only to
the repository maintainer and you, and is the preferred way to report anything that should not
be disclosed publicly before a fix is available.

If private reporting is not enabled on this repository when you look, please open a regular
issue asking the maintainer to enable it, without including vulnerability details in that
issue.

## Scope notes specific to this project

- `kai` is read-only by design: it has no code path that mutates, creates, deletes, or
  `exec`s/`attach`es/`port-forward`s into a Kubernetes resource. A report of a way to make it
  do so is a high-priority security issue.
- `kai` never reads `Secret` object values (only metadata/name references where relevant).
- Evidence bundle export is sanitized by default; the raw, unsanitized export path
  (`--allow-unsanitized-bundle-export`) is intentionally opt-in and documented as such in the
  README. A report that the *default* export leaks a credential is a high-priority issue; a
  report that the explicitly-opt-in raw export contains raw cluster data is expected behavior,
  not a vulnerability.
- Custom knowledge packs (`--knowledge`) and the Markdown runbooks they reference are treated
  as untrusted input: loading one must never execute code, shell commands, or expressions, and
  a referenced runbook path must never resolve outside the knowledge pack's own directory. A
  report of a way to break either guarantee is a high-priority issue.
- The optional AI-hypothesis layer (`--with-ai`) sends already-collected, best-effort-redacted
  evidence to an externally configured AI provider only when explicitly enabled. A report that
  this redaction is bypassable, or that evidence is sent when `--with-ai` was not passed, is a
  high-priority issue.
