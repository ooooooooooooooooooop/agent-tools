"""Cross-process, account-level request pacing.

ChatGPT throttles per-account: bursts of sends AND conversation-record
fetches trip its "请求过于频繁 / 已暂时限制你访问对话记录" interstitial
(HTTP 429 on the backend API). The existing ``retry_on_rate_limit`` only
reacts AFTER the popup appears; this module prevents reaching it: every
process (REST daemon, MCP pool slots, one-off scripts) shares one pace file,
so the account sees a bounded request rate no matter which process asks.

State: ``~/.chatgpt_web2api/request_pace.json``

    {"last_send_at": f, "last_read_at": f, "cooldown_until": f}

This is pacing, not a lock — a lost read-modify-write race merely loosens
pacing slightly. Each pace() re-reads the file after sleeping so a peer's
update during our wait is honored.
"""
import asyncio
import json
import os
import time
from pathlib import Path

from .tab_registry import REGISTRY_DIR

PACE_PATH = REGISTRY_DIR / "request_pace.json"

DEFAULT_SEND_INTERVAL = 30.0
DEFAULT_READ_INTERVAL = 8.0
DEFAULT_COOLDOWN_SECONDS = 300.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


class RequestPace:
    """Shared minimum-interval gate for account-visible requests.

    kind="send": a message POST (type+click) — the expensive, throttled path.
    kind="read": a /backend-api/* fetch (conversation fetch, lists, project
    ops) — cheaper but also rate-limited ("限制访问对话记录").
    """

    def __init__(
        self,
        send_interval: float | None = None,
        read_interval: float | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        self.send_interval = (
            send_interval
            if send_interval is not None
            else _env_float("W2A_PACE_SEND_S", DEFAULT_SEND_INTERVAL)
        )
        self.read_interval = (
            read_interval
            if read_interval is not None
            else _env_float("W2A_PACE_READ_S", DEFAULT_READ_INTERVAL)
        )
        self.cooldown_seconds = (
            cooldown_seconds
            if cooldown_seconds is not None
            else _env_float("W2A_PACE_COOLDOWN_S", DEFAULT_COOLDOWN_SECONDS)
        )
        self._lock = asyncio.Lock()

    def _read_state(self) -> dict:
        try:
            d = json.loads(PACE_PATH.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}

    def _write_state(self, state: dict) -> None:
        try:
            PACE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PACE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, PACE_PATH)
        except OSError:
            pass

    async def pace(self, kind: str) -> float:
        """Sleep until the shared gate lets this request through.

        Returns seconds waited. Stamps ``last_<kind>_at`` on the way out.
        """
        interval = self.send_interval if kind == "send" else self.read_interval
        waited = 0.0
        async with self._lock:
            for _ in range(4):
                state = self._read_state()
                now = time.time()
                due = max(
                    state.get("cooldown_until", 0.0),
                    state.get(f"last_{kind}_at", 0.0) + interval,
                )
                wait = due - now
                if wait <= 0:
                    break
                waited += wait
                await asyncio.sleep(wait)
            state = self._read_state()
            state[f"last_{kind}_at"] = time.time()
            self._write_state(state)
        return waited

    def record_throttle(self, seconds: float | None = None) -> float:
        """Set a shared cooldown after a throttle/429 signal.

        Any process that observes ChatGPT's rate-limit popup or a backend 429
        writes ``cooldown_until``; every other process honors it on its next
        paced call. Returns the cooldown_until timestamp.
        """
        state = self._read_state()
        until = time.time() + (seconds if seconds and seconds > 0 else self.cooldown_seconds)
        if until > state.get("cooldown_until", 0.0):
            state["cooldown_until"] = until
            self._write_state(state)
        return until
