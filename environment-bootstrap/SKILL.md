---
name: environment-bootstrap
description: Restore a known set of Codex skills from a source repository to another device or user-level skill directory with a read-only audit, explicit apply step, SHA-256 verification, and no deletion of destination-only files. Use when reproducing a Codex work environment, comparing installed skills with a backup repository, or preparing a safe cross-device restore.
---

# Environment Bootstrap

Use this skill for controlled skill-environment restoration. The source repository is authoritative, but the destination and write mode must be explicit.

## Workflow

1. Confirm the source repository contains `skills.json` and the registered package directories.
2. Run a read-only source audit:

   ```bash
   python3 scripts/validate_repo.py --strict
   ```

3. Resolve the destination explicitly. On Windows this is commonly `C:\Users\<user>\.codex\skills`; do not infer another user's directory.
4. Compare packages by hash:

   ```bash
   python3 scripts/sync_skills.py --destination "<destination>" --check
   ```

5. If the user explicitly requested restore, run `--apply`, then run `--check` again. Use `--skill <name>` for a narrow restore when appropriate.

## Safety boundary

- Never delete destination-only files as part of restore.
- Never install plugins, MCP servers, packages, hooks, or global configuration as an implicit side effect.
- Never report success from an old destination file, stdout alone, or an apply command without the post-apply check.
- If source validation fails, stop the restore and report the source error.
- For rollback, restore from a known-good source revision or backup and re-run the check; do not use recursive deletion.

## Output contract

Report source, destination, mode (`check` or `apply`), package counts, missing/different/extra files, hash-check result, and remaining risks. Use `PASS` only when the final check is clean, `PARTIAL` when an intentional non-blocking difference remains, and `BLOCKED` when the source, destination, permissions, or explicit write intent is missing.

## Verification

The minimum proof of a successful restore is:

```text
strict source validation: PASS
apply: completed (only when explicitly authorized)
post-apply hash check: PASS
destination-only files: preserved and reported
```

See [restore-profile.md](references/restore-profile.md) for the compact cross-device checklist.
