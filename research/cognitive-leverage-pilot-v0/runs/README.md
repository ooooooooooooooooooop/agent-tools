# Runs Directory (Cognitive Leverage Pilot V0)

This directory is reserved for execution traces and artifacts generated during future experimental runs of Pilot V0.

## Rules for Phase A
- **No baseline execution is performed during Phase A.**
- This directory remains clean of runtime evaluation data until Phase B execution is formally authorized.
- When runs are executed in Phase B, each run will be stored in a directory structured as:
  `runs/<case_id>/<baseline_id>/run-<timestamp>/`
  containing:
  - `trace.jsonl` (streaming machine trace adhering to `trace_schema.json`)
  - `artifacts/` (files produced by the run)
  - `metrics.json` (resource usage and latency metrics)
  - `verdict.json` (evaluation outcome according to `outcome_contract.md`)
