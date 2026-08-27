"""Unit tests for scripts/trace_identity.py (fixture-based; physical evidence separate)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import trace_identity  # noqa: E402


def write_session(tmp: Path, events: list) -> Path:
    p = tmp / "session.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return p


class TestTraceIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_consistent(self):
        p = write_session(self.tmp, [
            {"type": "session/header", "data": {"header": {"config": {"provider": "cpa", "model": "m1"}}}},
            {"type": "assistant/message", "data": {"message": {"source": {"provider": "cpa", "model": "m1"}}}},
        ])
        r = trace_identity.project(p, {"gateways": {}})
        self.assertEqual(r["requested_model"], {"provider": "cpa", "model": "m1"})
        self.assertEqual(r["provider_reported_model"]["model"], "m1")
        self.assertEqual(r["identity_assessment"]["status"], "consistent")

    def test_suspicious_on_swap(self):
        p = write_session(self.tmp, [
            {"type": "session/header", "data": {"header": {"config": {"provider": "cpa", "model": "m1"}}}},
            {"type": "assistant/message", "data": {"message": {"source": {"provider": "cpa", "model": "mX"}}}},
        ])
        r = trace_identity.project(p, {"gateways": {}})
        self.assertEqual(r["identity_assessment"]["status"], "suspicious")

    def test_gateway_alias_resolution(self):
        gw = {"gateways": {"cc": {"alias_map": {"claude-opus-4-8": "gpt-5.6-luna"}}}}
        p = write_session(self.tmp, [
            {"type": "session/header", "data": {"header": {"config": {"provider": "any", "model": "claude-opus-4-8"}}}},
            {"type": "assistant/message", "data": {"message": {"source": {"provider": "any", "model": "gpt-5.6-luna"}}}},
        ])
        r = trace_identity.project(p, gw)
        self.assertEqual(r["gateway_resolved_model"]["model"], "gpt-5.6-luna")
        self.assertEqual(r["identity_assessment"]["status"], "consistent")

    def test_unknown_without_reported(self):
        p = write_session(self.tmp, [
            {"type": "session/header", "data": {"header": {"config": {"provider": "cpa", "model": "m1"}}}},
        ])
        r = trace_identity.project(p, {"gateways": {}})
        self.assertEqual(r["identity_assessment"]["status"], "unknown")

    def test_no_forbidden_fields(self):
        p = write_session(self.tmp, [])
        r = trace_identity.project(p, {"gateways": {}})
        self.assertNotIn("verified_model", r)
        self.assertNotIn("verification_confidence", r)


if __name__ == "__main__":
    unittest.main()
