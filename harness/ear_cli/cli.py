from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from harness.http_client import post_json

DEFAULT_SERVER = os.getenv("OIDA_SERVER_URL") or os.getenv("HMM_SERVER_URL") or os.getenv("AEAR_SERVER_URL", "http://127.0.0.1:8765")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ear", description="Thin CLI for the OIDA perception daemon.")
    parser.add_argument("--server", default=argparse.SUPPRESS)
    parser.add_argument("--output", "-o", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    _path_command(sub, "report")
    transcribe = _path_command(sub, "transcribe")
    transcribe.add_argument("--ts", choices=["none", "sentence", "word"], default="sentence")
    _path_command(sub, "events")
    caption = _path_command(sub, "caption")
    caption.add_argument("--detail", choices=["brief", "dense"], default="dense")
    _path_command(sub, "speech")
    _path_command(sub, "music")
    qa = _path_command(sub, "qa")
    qa.add_argument("question")
    qa.add_argument("--thinking", type=int, default=None)
    think = _path_command(sub, "think")
    think.add_argument("instruction")
    think.add_argument("--thinking", type=int, default=None)

    args = parser.parse_args()
    if not hasattr(args, "server"):
        args.server = DEFAULT_SERVER
    if not hasattr(args, "output"):
        args.output = None
    result = dispatch(args)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _path_command(sub: argparse._SubParsersAction, name: str) -> argparse.ArgumentParser:
    cmd = sub.add_parser(name)
    cmd.add_argument("path")
    cmd.add_argument("--server", default=argparse.SUPPRESS)
    cmd.add_argument("--output", "-o", default=argparse.SUPPRESS)
    return cmd


def dispatch(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "report":
        return post_json(args.server, "/report", {"path": args.path, "profile": "default"})
    if args.command == "transcribe":
        return post_json(args.server, "/transcribe", {"path": args.path, "timestamps": args.ts})
    if args.command == "events":
        return post_json(args.server, "/events", {"path": args.path})
    if args.command == "caption":
        return post_json(args.server, "/caption", {"path": args.path, "detail": args.detail})
    if args.command == "speech":
        return post_json(args.server, "/speech", {"path": args.path})
    if args.command == "music":
        return post_json(args.server, "/music", {"path": args.path})
    if args.command == "qa":
        return post_json(args.server, "/qa", {"path": args.path, "question": args.question, "thinking_budget": args.thinking})
    if args.command == "think":
        return post_json(args.server, "/think", {"path": args.path, "instruction": args.instruction, "thinking_budget": args.thinking})
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
