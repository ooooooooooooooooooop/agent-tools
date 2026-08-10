# Example: Restore on a second device

User request:

> I copied this repository to a new Windows machine. Check the installed skills first, then restore the registered packages to `C:\Users\me\.codex\skills`.

Expected behavior:

1. Validate the source repository.
2. Compare the explicit destination without writing.
3. Apply only because the user explicitly requested restore.
4. Run the post-apply hash check and report any destination-only files without deleting them.
