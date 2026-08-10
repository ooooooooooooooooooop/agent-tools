---
name: skill-repository-maintainer
description: Audit and maintain a repository of Codex skills: inspect package layout, validate the manifest and metadata, detect private/runtime files, review organization, and explicitly synchronize registered packages to another device. Use when maintaining a skill backup repository, checking whether skills are complete, preparing a release, or comparing a source repository with an installed skill directory.
---

# Skill Repository Maintainer

Use this skill as the read-only control plane for a Codex skill backup repository.

## Workflow

1. Identify the repository root and the intended destination, if any. Do not guess a destination for a write.
2. Inventory `skills.json`, root-level packages, `SKILL.md`, `agents/openai.yaml`, examples, references, scripts, assets, and ignored runtime state.
3. Run the repository validator when present:

   ```bash
   python3 scripts/validate_repo.py --strict
   ```

4. Review the diff and package boundary. Keep private memory, caches, generated reports, machine-specific paths, and temporary files out of published packages.
5. For a destination audit, run `scripts/sync_skills.py --check` and report missing, different, and destination-only files.
6. Only when the user explicitly requests synchronization, run the same command with `--apply`, then run `--check` again. Never delete destination-only files as part of sync.

Use the bundled `scripts/audit.py` when the repository does not yet provide its own validator. It is read-only and intentionally reports an incomplete layout instead of silently repairing it.

## Architecture decisions

- Treat the manifest as the registry and root-level package directories as the source of truth.
- Prefer small, reversible changes. Do not reorganize packages or rename skills merely for visual symmetry.
- Keep deterministic checks in scripts and detailed policy in references; keep `SKILL.md` under 500 lines.
- Dependencies in the manifest describe routing/composition only; they do not authorize installation or automatic execution.

## Output contract

Report one of:

- `PASS`: all requested checks passed and evidence is listed;
- `PARTIAL`: read-only audit passed but an optional repair/sync remains;
- `BLOCKED`: a required source, manifest, permission, or safety condition is missing.

Include the repository path, destination (if checked), commands or scripts run, changed files (if any), unresolved risks, and whether the operation was read-only. Do not call a stale destination “synchronized” without a post-apply check.

## Verification

At minimum, run the strict validator, the relevant package smoke tests, and a read-only destination check. For an apply operation, require an immediate second check and preserve destination-only files.
