# Vendored fork notice

Source: <https://github.com/Octo-Lex/ChatGPT-Web2API> @ `497527d` (MIT, Elephant Rock Lab).

Local delta carried in this copy (applied on top of upstream):

- **conv-affinity**: a tab's URL is its conversation identity — `/c/{id}` tabs are
  persistent shared resources adopted across sessions/processes; one tab per
  conversation; conv-bound tabs are never navigated away or closed by drivers.
- **background tab creation**: `Target.createTarget` uses `background:true` —
  the bridge never steals window focus.
- **request pacing** (`request_pace.py`): cross-process account-level throttle —
  send ≥30s, backend reads ≥8s, shared cooldown on 429. Prevents the
  「请求过于频繁/限制访问对话记录」 interstitial instead of reacting to it.
- **project name resolution** (`resolve_project_id`): `project_id` accepts an
  exact project name; unknown/ambiguous names fail instead of landing in the
  wrong project.
- **upstream fixes**: temp-route misrouting, zh placeholder premature
  completion detection.
- **composer multiline insert** (`chatgpt_dom._insert_text`): CDP
  `Input.insertText` truncates at the first `\n` on the current
  conversation-page composer (only the first paragraph lands — observed
  live 2026-09-14 after a ChatGPT frontend update; the new-chat page
  composer still splits paragraphs correctly). Insert now goes through
  `document.execCommand('insertText')`, which routes through the editor's
  own text-insertion path and produces the block structure the canonical
  verifier reads back.
- `start.ps1`: persistent-Chrome topology launcher (Chrome standalone,
  daemons attach; restarting daemons never touches the browser/login).
- **lazy Chrome bring-up in MCP**: both MCP driver paths (session-pool
  `_create_driver`, singleton `run_mcp`) call `ChromeProcess.ensure_running()`
  before connecting, so a stdio-registered server cold-launches Chrome on
  first tool call — harness-bound lifecycle, zero resident daemons when the
  tool is never invoked. `install.ps1` resolves the venv via `W2A_VENV`
  (repo-external) else package-local `.venv`, matching `start.ps1`.
- **reply-persistence reporting** (`chat_completion`, `chat_with_gpt`):
  post-send tail check adds `reply_persisted` to the result —
  true = assistant reply persisted; false = tail is still the caller's own
  user message, i.e. the generation died mid-stream (the empirical recovery
  is a short nudge like 「继续」 in the same conversation, not polling —
  observed 2026-09-14: an agent hand-polled a dead generation for ~7 min);
  null = inconclusive. Failed/ambiguous fetches can never crash a
  successful send.
- **`wait_reply` tool**: blocks until an assistant message persists,
  `timeout_seconds` hits, or the tail stays the caller's user message past
  `dead_after_seconds` (default 120) → `status:"dead"` early exit.
  `since_total` accepts a prior `get_conversation` `total` to wait only for
  a NEW reply. Replaces hand-rolled get_conversation+sleep polling loops.
- **`get_conversation` disambiguation + file channel**: results now carry
  `reason` (`ok` / `empty` / `not_found` / `fetch_failed`) — 404s and fetch
  errors no longer masquerade as empty conversations — and `out_file`
  (absolute path) writes the page to disk so long replies never have to
  cross the MCP tool-result budget.
- **`wait_reply` terminal-status gate**: a persisted assistant node whose
  backend status is still `in_progress` no longer counts as "replied" —
  the early-intro false positive (observed 2026-09-15) that made callers
  consume partial replies and misdiagnose a live generation as dead. The
  result gains `tail_status`; on `timeout`, `in_progress` means the web
  side is still generating (wait again, don't nudge).
- **composer draft auto-clear**: any failure inside the send window
  (type → click → send-ack), including client-side cancel, best-effort
  clears the composer — a failed send can no longer leave a draft that
  poisons the next send's canonical verification (observed 2026-09-15:
  recovery previously required manual `evaluate_script` surgery).

Runtime state is NOT vendored: `.venv`, `~/.chatgpt_web2api/` (config, tab
registry, pace file), Chrome profile, and conversation ids live per-device /
per-account.
