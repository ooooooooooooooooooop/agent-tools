#!/usr/bin/env python3
"""Evolution Route Tuner — Phase 3: L3 routing parameter grid search.

Grid-search golden-live evaluation parameters (model, temperature, max_tokens)
over the CPA relay, using golden-live pass rate + cost as fitness.

Usage:
    python scripts/evolution_route_tune.py                        # full grid (all models)
    python scripts/evolution_route_tune.py --quick                # 2 models only
    python scripts/evolution_route_tune.py --models gemini-3.7-flash-high,gpt-5.6-luna-max
    python scripts/evolution_route_tune.py --dry-run              # print param grid only
    python scripts/evolution_route_tune.py --report-only          # re-read last results

Output: writes to ~/.agent-broker/topics/skills/evolution-inbox/proposals/route-tune-<stamp>.json
"""

import json, os, sys, time, urllib.request, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_SET = ROOT / "scripts" / "evals" / "golden_set.json"
INBOX_DIR = Path.home() / ".agent-broker" / "topics" / "skills" / "evolution-inbox"
PROPOSALS_DIR = INBOX_DIR / "proposals"
CPA_BASE = os.environ.get("CPA_BASE", "http://127.0.0.1:8317/v1")
CPA_MODEL_DEFAULT = os.environ.get("CPA_GOLDEN_MODEL", "gpt-5.6-luna-max")
CPA_TIMEOUT = 90

# ---- Parameter grid ----
# Model: only models known to work on the CPA relay (from settings.yaml)
DEFAULT_GRID = {
    "model": [
        "gpt-5.6-luna-max",       # default cheap worker
        "gemini-3.7-flash-high",  # flash alternative
        # "claude-sonnet-4-6",    # disabled: expensive, no CJK edge
        # "gpt-5.6-sol-xhigh",    # frontier, too expensive for golden-live
    ],
    "temperature": [0.0],
    "max_tokens": [300],
}
QUICK_GRID = {
    "model": ["gpt-5.6-luna-max", "gemini-3.7-flash-high"],
    "temperature": [0.0],
    "max_tokens": [300],
}

def _cpa_key() -> str:
    key = os.environ.get("CPA_API_KEY")
    if key:
        return key
    creds = Path.home() / ".dsh" / ".credentials.yaml"
    if creds.is_file():
        import re
        m = re.search(r"CPA_API_KEY\s*[:=]\s*['\"]?(\w+)['\"]?", creds.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return ""

def _cpa_complete(model: str, prompt: str, max_tokens: int = 300, temperature: float = 0.0) -> str:
    """One bounded chat completion against the CPA relay."""
    body = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the evaluation judge for this machine's agent system. "
                    "Follow the user instruction and reply with the requested "
                    "structured output. Do not output reasoning or markdown fences."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{CPA_BASE}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_cpa_key()}",
        },
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=CPA_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    elapsed = time.time() - t0
    try:
        content = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage", {}) or {}
        tokens = {
            "prompt": usage.get("prompt_tokens", 0) or 0,
            "completion": usage.get("completion_tokens", 0) or 0,
        }
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected CPA response: {exc}") from exc
    return content, tokens, elapsed

def _run_golden_live(model: str, max_tokens: int = 300, temperature: float = 0.0) -> dict:
    """Run golden-live with specific model params, return {passed, total, tokens, elapsed, failures}."""
    if not GOLDEN_SET.is_file():
        return {"error": "golden_set.json missing"}
    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8-sig"))
    total = passed = 0
    prompt_tokens = completion_tokens = 0
    total_elapsed = 0.0
    failures = []
    for entry in golden.get("skills", []):
        name = entry.get("name")
        for case in entry.get("cases", []):
            total += 1
            prompt = case.get("prompt", "")
            exp = case.get("expect", {})
            includes = exp.get("includes", [])
            excludes = exp.get("excludes", [])
            include_txt = ", ".join(includes) if includes else "(none)"
            exclude_txt = ", ".join(excludes) if excludes else "(none)"
            judge_prompt = (
                f"Skill under test: {name}. A user asks: \"{prompt}\"\n"
                f"Judge whether a CORRECT assistant response (following the skill) "
                f"would satisfy ALL of these criteria: [{include_txt}] "
                f"and AVOID ALL of: [{exclude_txt}].\n"
                'Reply with ONLY a JSON object: {"pass": true or false, "reason": "<short reason>"}.'
            )
            try:
                answer, tokens, elapsed = _cpa_complete(model, judge_prompt, max_tokens, temperature)
            except Exception as exc:
                failures.append(f"{name}:{case.get('id')} error: {exc}")
                continue
            prompt_tokens += tokens["prompt"]
            completion_tokens += tokens["completion"]
            total_elapsed += elapsed
            try:
                verdict = json.loads(answer.strip())
            except (json.JSONDecodeError, ValueError):
                failures.append(f"{name}:{case.get('id')} non-JSON: {answer[:80]}")
                continue
            if isinstance(verdict, dict) and verdict.get("pass") is True:
                passed += 1
            else:
                reason = verdict.get("reason") if isinstance(verdict, dict) else "no reason"
                failures.append(f"{name}:{case.get('id')} FAIL: {reason}")
    return {
        "passed": passed,
        "total": total,
        "pass_rate": round(100 * passed / total, 1) if total else 0,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "elapsed_seconds": round(total_elapsed, 1),
        "failures": failures,
    }

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="2-model grid only")
    ap.add_argument("--models", help="comma-separated model list override")
    ap.add_argument("--dry-run", action="store_true", help="print param grid and exit")
    ap.add_argument("--report-only", action="store_true", help="re-read last result, don't run")
    args = ap.parse_args()

    if not _cpa_key():
        print("ERROR: CPA_API_KEY not set")
        return 1

    # Build grid
    if args.models:
        models = [m.strip() for m in args.models.split(",")]
        grid = {"model": models, "temperature": [0.0], "max_tokens": [300]}
    elif args.quick:
        grid = QUICK_GRID
    else:
        grid = DEFAULT_GRID

    params_list = []
    for m in grid["model"]:
        for t in grid["temperature"]:
            for mt in grid["max_tokens"]:
                params_list.append({"model": m, "temperature": t, "max_tokens": mt})
    print(f"route-tune: {len(params_list)} parameter combination(s)")
    for p in params_list:
        print(f"  {p['model']}  temp={p['temperature']}  max_tokens={p['max_tokens']}")

    if args.dry_run:
        return 0

    if args.report_only:
        # Find last report
        reports = sorted(PROPOSALS_DIR.glob("route-tune-*.json"))
        if not reports:
            print("no route-tune reports found")
            return 1
        last = reports[-1]
        print(f"reading last report: {last.name}")
        data = json.loads(last.read_text(encoding="utf-8"))
        print(f"  timestamp: {data.get('timestamp', '?')}")
        for r in data.get("results", []):
            print(f"  {r['model']}: {r['pass_rate']}% ({r['passed']}/{r['total']})  "
                  f"tokens={r['total_tokens']}  elapsed={r['elapsed_seconds']}s")
        return 0

    # Run grid
    results = []
    for p in params_list:
        print(f"  running golden-live with {p['model']} temp={p['temperature']} max_tokens={p['max_tokens']}...")
        r = _run_golden_live(p["model"], p["max_tokens"], p["temperature"])
        r.update(p)
        results.append(r)
        print(f"    -> {r['pass_rate']}% ({r['passed']}/{r['total']})  "
              f"tokens={r['total_tokens']}  elapsed={r['elapsed_seconds']}s")

    # Rank by fitness: pass_rate desc, then tokens asc, then elapsed asc
    results.sort(key=lambda r: (-r["pass_rate"], r["total_tokens"], r["elapsed_seconds"]))

    best = results[0] if results else None
    report = {
        "schema": "route-tune/v1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "grid": grid,
        "results": results,
        "best": best,
        "recommendation": (
            f"best config: {best['model']}  temp={best['temperature']}  "
            f"max_tokens={best['max_tokens']}  ({best['pass_rate']}%)"
            if best else "no results"
        ),
    }

    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    report_file = PROPOSALS_DIR / f"route-tune-{stamp}.json"
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nreport written: {report_file}")
    print(f"recommendation: {report['recommendation']}")

    # Print comparison table
    print("\n=== Comparison Table ===")
    print(f"| {'Model':<28} | {'T':<4} | {'MT':<5} | {'%':<5} | {'Tokens':<8} | {'Elapsed':<8} |")
    print(f"|{'-'*29}|{'─'*5}|{'─'*6}|{'─'*6}|{'─'*9}|{'─'*9}|")
    for r in results:
        marker = " ← best" if r == best else ""
        print(f"| {r['model']:<28} | {r['temperature']:<4} | {r['max_tokens']:<5} | "
              f"{r['pass_rate']:<5} | {r['total_tokens']:<8} | {r['elapsed_seconds']:<8} |{marker}")

    # If best differs from default, suggest proposal
    if best and best["model"] != CPA_MODEL_DEFAULT:
        print(f"\nNOTE: best model ({best['model']}) differs from default ({CPA_MODEL_DEFAULT}). "
              "Consider updating CPA_GOLDEN_MODEL env var or run_skill_evals.py default.")
    return 0

if __name__ == "__main__":
    sys.exit(main())