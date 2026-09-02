# DSH model capability reconciliation

Generated/non-canonical evidence report.

- routes: 16
- capability families: 11

## Capability matrix

| Family | Routes | Current context/output | Truth context/output | Effective context/output | Reserved | Status | Action |
|---|---|---|---|---|---:|---|---|
| any:claude-opus-5 | any:claude-opus-5 | [1000000]/[128000] | [1000000]/[128000] | [1000000]/[128000] | ['REQUEST_DYNAMIC'] | CORRECT | none |
| bai:deepseek-chat | bai:deepseek-v4-flash | [128000]/[8192] | [128000]/[8192] | [128000]/[8192] | ['REQUEST_DYNAMIC'] | CORRECT | none |
| cpa:claude-opus-4-6 | cpa:claude-opus-4-6-thinking | [200000]/[64000] | [200000]/[64000] | [200000]/[64000] | ['REQUEST_DYNAMIC'] | CORRECT | none |
| cpa:claude-sonnet-4-6 | cpa:claude-sonnet-4-6 | [200000]/[64000] | [200000]/[64000] | [200000]/[64000] | ['REQUEST_DYNAMIC'] | CORRECT | none |
| cpa:gemini-3.7-flash | cpa:compaction_summary, cpa:gemini-3.7-flash-high | [1048576]/[65536] | [1048576]/[65536] | [1048576]/[65536] | ['REQUEST_DYNAMIC'] | CORRECT | none |
| cpa:gpt-5.6-luna | cpa:gpt-5.6-luna-max, cpa:main_default, cpa:subagent_fork, cpa:subagent_spawn | [1050000]/[128000] | [1050000]/[128000] | [1050000]/[128000] | ['REQUEST_DYNAMIC'] | CORRECT | none |
| cpa:gpt-5.6-sol | cpa:gpt-5.6-sol, cpa:gpt-5.6-sol-xhigh | [1050000]/[128000] | [1050000]/[128000] | [1050000]/[128000] | ['REQUEST_DYNAMIC'] | CORRECT | none |
| cpa:gpt-image-2 | cpa:gpt-image-2 | []/[] | []/[] | []/[] | ['REQUEST_DYNAMIC'] | SPECIALIZED_MODALITY | none |
| deepseek-official:deepseek-v4-flash | deepseek-official:deepseek-v4-flash | []/[] | []/[] | []/[] | ['REQUEST_DYNAMIC'] | ROUTE_NOT_ADMITTED | evidence-required |
| kimi-coding:k3 | kimi-coding:k3-256k | [262144]/[] | [262144]/[] | [262144]/[] | ['REQUEST_DYNAMIC'] | OUTPUT_SEMANTICS_DYNAMIC | none |
| opencode-go:deepseek-v4-flash | opencode-go:deepseek-v4-flash | [1048576]/[393216] | [1048576]/[393216] | [1048576]/[393216] | ['REQUEST_DYNAMIC'] | CORRECT | none |

## Evidence gates

- This is a generated, non-canonical report; no capability value is inferred from DSH metadata alone.
- Independent capability receipts are required before a non-CORRECT route can enter an overall PASS.
- Remaining non-CORRECT routes: cpa/gpt-5.6-sol (ROUTE_NOT_ADMITTED), cpa/gpt-image-2 (SPECIALIZED_MODALITY), deepseek-official/deepseek-v4-flash (ROUTE_NOT_ADMITTED), kimi-coding/k3-256k (OUTPUT_SEMANTICS_DYNAMIC).
