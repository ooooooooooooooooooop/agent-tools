# Example: Audit a skill backup repository

User request:

> Check whether this skill repository is complete, identify files that should not be published, and tell me whether my installed skills match it. Do not delete anything.

Expected behavior:

1. Locate `skills.json` and list registered packages.
2. Run the strict validator and inspect ignored/runtime boundaries.
3. Run `scripts/sync_skills.py --check --destination <explicit destination>`.
4. Report `PASS`, `PARTIAL`, or `BLOCKED` with the exact evidence and keep the operation read-only.
