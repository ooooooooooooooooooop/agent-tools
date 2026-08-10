# Synchronization and Release

The repository is authoritative. Synchronization is an explicit copy operation from registered packages to a destination such as `C:\Users\yexue\.codex\skills`.

## Read-only audit

```bash
python3 scripts/validate_repo.py --strict
python3 scripts/sync_skills.py --destination "$CODEX_HOME/skills" --check
```

If `CODEX_HOME` is unset on Windows, use the actual configured Codex directory explicitly. The check compares every publishable source file by SHA-256 and reports missing, different, and destination-only files.

## Apply and verify

```bash
python3 scripts/sync_skills.py --destination "$CODEX_HOME/skills" --apply
python3 scripts/sync_skills.py --destination "$CODEX_HOME/skills" --check
```

Use `--skill <name>` one or more times for a narrow package sync. The script creates parent directories and replaces files atomically. It never deletes destination-only files, so stale files require a separate, explicitly reviewed cleanup decision.

## Rollback

Before applying a material package change, preserve the destination state if rollback matters. A rollback is another intentional copy from a known-good source revision or a restored backup, followed by `--check`. Do not use recursive deletion or broad directory replacement as a rollback mechanism.

## Release gate

Before commit or publishing:

1. `python3 scripts/validate_repo.py --strict` passes.
2. `python3 -m unittest discover -s tests -v` passes.
3. Python syntax and Markdown lint pass.
4. Relevant examples and package validators pass.
5. `sync_skills.py --check` is clean for each intended destination.
6. `git diff --check` and the final diff contain no private/runtime artifacts.

Repository maintenance does not commit, push, open PRs, install plugins, or change global configuration unless the user requests those actions separately.
