# Example: Ambiguous Repository Restructure

Goal:

Determine whether the repository should move published packages into a new directory.

Missing information:

- Whether the package installer supports the proposed nested layout.
- Whether existing installation commands must remain unchanged.

Assumptions:

- Existing uncommitted files belong to the user and must not be moved automatically.

Risks:

- Breaking clean-clone installation.
- Mixing repository cleanup with deletion of local work products.

Minimal path:

1. Inspect the current manifest and installer contract.
2. Add a read-only validation check.
3. Move packages only after a clean-clone smoke test passes.

Question:

Should the first change preserve root-level package paths for installer compatibility?
