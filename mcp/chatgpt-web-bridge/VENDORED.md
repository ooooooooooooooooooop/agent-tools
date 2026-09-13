# Vendored fork notice

Source: https://github.com/Octo-Lex/ChatGPT-Web2API @ `497527d` (MIT, Elephant Rock Lab).

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
- `start.ps1`: persistent-Chrome topology launcher (Chrome standalone,
  daemons attach; restarting daemons never touches the browser/login).
- **lazy Chrome bring-up in MCP**: both MCP driver paths (session-pool
  `_create_driver`, singleton `run_mcp`) call `ChromeProcess.ensure_running()`
  before connecting, so a stdio-registered server cold-launches Chrome on
  first tool call — harness-bound lifecycle, zero resident daemons when the
  tool is never invoked. `install.ps1` resolves the venv via `W2A_VENV`
  (repo-external) else package-local `.venv`, matching `start.ps1`.

Runtime state is NOT vendored: `.venv`, `~/.chatgpt_web2api/` (config, tab
registry, pace file), Chrome profile, and conversation ids live per-device /
per-account.
