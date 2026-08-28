#!/usr/bin/env python3
"""model_health.py — low-cost provider/model health canary.

Tiers: reachability = GET /v1/models (free, higher frequency OK);
capability canary = --deep only (低频/按需, uses cheapest catalog slot).
identity_assessment stays consistent/suspicious/unknown — never verified_model.
No paid-model calls in default mode.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import gov_log, now_iso, private_gateways  # noqa: E402


def probe(name: str, listen: str, timeout: float = 5.0) -> dict:
    t0 = time.time()
    try:
        with urllib.request.urlopen(f"http://{listen}/v1/models", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        models = [m["id"] for m in data.get("data", [])]
        return {"provider": name, "reachable": True,
                "latency_ms": round((time.time() - t0) * 1000),
                "model_count": len(models),
                "identity_assessment": "unknown",  # shape-only; no identity claims
                "request_success": True}
    except Exception as exc:  # noqa: BLE001
        return {"provider": name, "reachable": False,
                "latency_ms": round((time.time() - t0) * 1000),
                "request_success": False, "error": type(exc).__name__,
                "identity_assessment": "unknown"}


def main() -> int:
    results = []
    for name, gw in private_gateways().get("gateways", {}).items():
        if gw.get("listen"):
            r = probe(name, gw["listen"])
            results.append(r)
            print(f"{name}: reachable={r['reachable']} latency={r['latency_ms']}ms "
                  f"models={r.get('model_count', '-')} identity={r['identity_assessment']}")
    bad = [r for r in results if not r["reachable"]]
    gov_log("model_health", "ok" if not bad else "findings", results)
    print(f"providers={len(results)} unreachable={len(bad)} (identity: unknown unless proven "
          f"otherwise; canary does NOT assert real model identity)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
