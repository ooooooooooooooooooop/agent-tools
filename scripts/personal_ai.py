#!/usr/bin/env python3
"""Personal AI CLI — 统一命令行入口

Usage:
  personal-ai subtask-model [status | luna | gemini]
  personal-ai sync [check | sync | --detail]
  personal-ai status
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "aic"))
sys.path.insert(0, str(REPO / "scripts"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal-ai", description="Personal AI Command-Line Interface")
    sub = parser.add_subparsers(dest="command")

    # subtask-model command
    p_subtask = sub.add_parser("subtask-model", help="Switch or inspect subtask execution model (luna / gemini / status)")
    p_subtask.add_argument("profile", nargs="?", choices=["luna", "gemini", "status"], default="status",
                           help="Target profile (luna / gemini) or status query")

    args, extra = parser.parse_known_args(argv)

    if args.command == "subtask-model":
        import subtask_model_switch
        return subtask_model_switch.main([args.profile, *extra])

    # Default: if first arg is 'subtask-model', dispatch directly
    raw_args = list(argv or sys.argv[1:])
    if raw_args and raw_args[0] == "subtask-model":
        import subtask_model_switch
        return subtask_model_switch.main(raw_args[1:])

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
