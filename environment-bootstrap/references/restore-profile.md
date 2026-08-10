# Restore Profile

Use this checklist when reproducing the repository on another device:

1. Source path is explicit and contains `skills.json`.
2. `python3 scripts/validate_repo.py --strict` passes.
3. Destination is explicit and outside the source repository.
4. `python3 scripts/sync_skills.py --destination <destination> --check` records the baseline.
5. Apply is executed only after the user requests restore.
6. The same destination is checked again after apply.
7. Missing and different source files are clean; destination-only files are preserved and reported.
