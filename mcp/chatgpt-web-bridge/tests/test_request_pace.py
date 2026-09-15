"""RequestPace kind-split: read-path 429 vs send-path rate limit.

ChatGPT's conversation-endpoint limiter (``/backend-api/conversation*`` →
"限制访问对话记录") is endpoint-scoped — upstream keeps answering sends
while conversation fetches 429 — so a read-path throttle gates reads only.
A send-path rate limit (the UI popup) is the account-wide signal and gates
both kinds.
"""

import asyncio
import json
import time

import pytest

from chatgpt_web2api import request_pace as rp


@pytest.fixture
def pace_file(tmp_path, monkeypatch):
    p = tmp_path / "request_pace.json"
    monkeypatch.setattr(rp, "PACE_PATH", p)
    return p


@pytest.fixture
def no_sleep(monkeypatch):
    """Make pace()'s cooldown waits instant; ``waited`` still accumulates."""

    async def _nosleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _nosleep)


@pytest.mark.asyncio
async def test_read_throttle_gates_reads_but_not_sends(pace_file, no_sleep):
    pace = rp.RequestPace(send_interval=0, read_interval=0, cooldown_seconds=60)
    pace.record_throttle(kind="read", source="test")

    state = json.loads(pace_file.read_text())
    assert state["read_cooldown_until"] > time.time() + 30
    assert state.get("cooldown_until", 0) <= time.time()

    assert await pace.pace("send") == 0.0  # sends are not gated by a read 429
    assert await pace.pace("read") > 30.0  # reads ride out the cooldown


@pytest.mark.asyncio
async def test_send_throttle_gates_both_kinds(pace_file, no_sleep):
    pace = rp.RequestPace(send_interval=0, read_interval=0, cooldown_seconds=60)
    pace.record_throttle(kind="send", source="test")

    state = json.loads(pace_file.read_text())
    assert state["cooldown_until"] > time.time() + 30

    assert await pace.pace("send") > 30.0
    assert await pace.pace("read") > 30.0


def test_record_throttle_default_kind_is_account_wide(pace_file):
    rp.RequestPace().record_throttle(source="t")
    state = json.loads(pace_file.read_text())
    assert "cooldown_until" in state
    assert "read_cooldown_until" not in state


def test_read_throttle_never_shortens_existing(pace_file):
    pace = rp.RequestPace(cooldown_seconds=60)
    first = pace.record_throttle(kind="read", source="a")
    pace.record_throttle(5, kind="read", source="b")  # shorter — must lose
    state = json.loads(pace_file.read_text())
    assert state["read_cooldown_until"] == pytest.approx(first, abs=0.5)


def test_read_and_send_cooldowns_are_independent(pace_file):
    """A read 429 must not erase (or be erased by) a send-path cooldown."""
    pace = rp.RequestPace(cooldown_seconds=60)
    pace.record_throttle(kind="send", source="s")
    pace.record_throttle(120, kind="read", source="r")
    state = json.loads(pace_file.read_text())
    assert state["cooldown_until"] > time.time() + 30
    assert state["read_cooldown_until"] > time.time() + 90
