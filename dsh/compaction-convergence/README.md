# DSH Compaction Convergence Overlay

Target:
- DSH `0.1.1-rc.2`
- `@deepseek-ai/dsh-compaction-basic@0.1.1-rc.2`

Fixes (see `REPORT.md`):
- **FIX A — checkpoint-aware region selection**: `selectCompactableRange` never re-selects a
  compaction checkpoint as a shadowed source; an all-checkpoint candidate region returns `null`.
- **FIX B — pressure convergence**: after a `summary is not smaller` failure on an unchanged
  region/fingerprint, later pre-step pressure events do not re-invoke the summarizer; any surface
  mutation re-enables evaluation.

## Contents

- `lib/` — patched package source (same package name, `version 0.1.1-rc.2+conv.1`)
- `test/` — 12-case node:test suite, offline session replay, runtime smoke,
  `install-convergence.ps1`, `restore-convergence.ps1`, `guard-convergence.ps1`,
  `run-tests.ps1`
- `REPORT.md` — forensic evidence and acceptance record

## Lifecycle

Entry point: `test/guard-convergence.ps1` (idempotent; safe as part of bootstrap/lifecycle).

Deployment shape (survives GUI restarts, which rebuild the npm-cache checkout):
- fork lives in `~/.dsh/profiles/web/plugins/dsh-compaction-convergence` (profile-owned)
- loader overlay row uses its own id `compaction-basic-convergence` pointing at
  `./plugins/dsh-compaction-convergence/lib/index.js` (explicit file target; the upstream
  `compaction-basic` host row stays `disabled: true`)
- dependency resolution reuses the checkout's `@deepseek-ai/*` scope through the profile
  node_modules chain (junction fallback, rebuilt idempotently)

Verdicts:
- `VERIFY` (exit 0) — affected version already patched; module version, lib SHA-256, marker,
  patch wiring and dependency resolution all match.
- `APPLY` (exit 1) — affected version `0.1.1-rc.2` without overlay; installer ran, rerun to VERIFY.
- `NOT_REQUIRED` (exit 0) — running version differs and upstream already contains both fixes
  (checkpoint-aware selection + pressure convergence).
- `REVIEW` (exit 2) — running version differs and compatibility cannot be proven; do not patch blindly.

Guarantees:
- Idempotent install (`install-convergence.ps1` copies the fork into the profile, wires the
  loader row exactly once, and re-creates the dependency junction only when resolution fails).
- Rollback: `restore-convergence.ps1` removes the profile fork, strips the overlay block from
  `cordis.patch.yml`, and removes the junction (hash-verified).
- Canonical source lives here; the npm cache is never edited by hand — only scripted overlay.
- No secrets involved (no credentials, keys, or session data in this directory).
- After install or restore, restart the DSH GUI process so the running process loads the new module.

## New-device restore

Bootstrap flow (see `BOOTSTRAP.md`) runs this overlay automatically after the repo is cloned:
1. `git clone agent-tools` → validate → install skills
2. `<repo>/dsh/compaction-convergence/test/guard-convergence.ps1` → APPLY / VERIFY

## Upstream exit condition

When upstream `@deepseek-ai/dsh-compaction-basic` ships FIX A + FIX B (checkpoint-aware selection
and unchanged-region pressure convergence), the guard reports `NOT_REQUIRED` and the overlay is
retired. Until then, the overlay pins the known-good behavior for `0.1.1-rc.2`.