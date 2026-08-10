# Example: Small Bug Fix

Request:

Fix a parser that drops the last record in a file.

Minimal path:

- Change the loop boundary in the shared parser.
- Add one regression case for a one-record file and an empty file.
- Run the parser tests and report the exact result.

Intentionally not changed:

- No new parser abstraction.
- No unrelated formatting or dependency updates.
