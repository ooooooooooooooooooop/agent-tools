# Research Lab DSH Adapter

This package is a thin Cordis adapter for the Research Lab Python CLI. It registers
`research_init`, `research_create`, `research_inspect`, `research_validate`, `research_execute`,
`research_status`, `research_evidence`, `research_compare`, `research_continue`,
`research_verify`, `research_sync-init`, `research_sync-push`, and `research_sync-pull`.

The adapter owns no research state. Each tool starts the configured Python CLI with
JSON output enabled and returns the stable `{ ok, code, message, details,
protocolVersion }` envelope.

## Configuration

`apply(ctx, config)` accepts:

- `workspace`: default workspace path passed to workspace-aware commands.
- `python`: Python executable (default: `python`, or `RESEARCH_PYTHON`).
- `cliModule` / `cli`: a relative CLI script path, Python module name, or argument
  array. The default is the installed Python module `research_lab` (`python -m research_lab`).
- `cwd`: optional process working directory.
- `env`: optional environment additions.
- `spawn`: optional child-process-compatible function for host tests.

A tool input can override `workspace` and supplies command-specific fields such as
`spec`, `researchId`, `runId`, and `evidenceId`. Install the Python `research-lab`
package separately on the device; the DSH bundle deliberately does not embed or
own the Core.

## Lifecycle

The returned function disposes only the eight tool registrations. It does not own or
persist Research Workspace data.

## Local verification

From the repository root:

```text
node --test hosts/dsh/tests/index.test.mjs
```

From this package directory:

```text
node --test tests/index.test.mjs
```
