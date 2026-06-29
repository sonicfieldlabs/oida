from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

from harness.akouo.command import build_command_output
from harness.akouo.loader import AkouoLoader
from harness.benchmark import run_report_benchmark
from harness.corpus import answer_timeline_question, load_timeline, timeline_entry, timeline_path_for_folder, write_timeline
from harness.dialog import append_turn, context_text, default_session_id, load_session, save_session, session_path
from harness.http_client import get_json, post_json
from harness.journal import append_lexicon_entry, render_journal, write_json, write_session

AUDIO_EXTS = {".wav", ".wave", ".aiff", ".aif", ".flac", ".mp3", ".m4a", ".ogg"}
DEFAULT_SERVER = os.getenv("AEAR_SERVER_URL", "http://127.0.0.1:8765")
DEFAULT_REPO = str(Path(__file__).resolve().parents[1])


def main() -> None:
    parser = argparse.ArgumentParser(prog=Path(sys.argv[0]).name, description="hmm local listening harness.")
    parser.add_argument("--server", default=argparse.SUPPRESS)
    parser.add_argument("--repo", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command_name", required=True)

    listen = sub.add_parser("listen", help="Run one routed listening session.")
    listen.add_argument("path")
    listen.add_argument("--command", default="/listen")
    listen.add_argument("--validate", action="store_true")
    add_common_options(listen)

    chat = sub.add_parser("chat", help="Ask one or more questions of one clip and append them to a journal.")
    chat.add_argument("path")
    chat.add_argument("--question", action="append", default=[])
    chat.add_argument("--thinking", type=int, default=512)
    chat.add_argument("--session", default=None)
    chat.add_argument("--reset", action="store_true")
    add_common_options(chat)

    sweep = sub.add_parser("sweep", help="Batch reports over a folder and build a lexicon JSONL.")
    sweep.add_argument("folder")
    sweep.add_argument("--limit", type=int, default=None)
    add_common_options(sweep)

    corpus_qa = sub.add_parser("corpus-qa", help="Answer a question from a merged sweep timeline.")
    corpus_qa.add_argument("target")
    corpus_qa.add_argument("question")
    corpus_qa.add_argument("--max-results", type=int, default=8)
    corpus_qa.add_argument("--output", default=None)
    add_common_options(corpus_qa)

    live = sub.add_parser("live", help="Start, inspect, stop, or describe a local live ring-buffer/VAD session.")
    live.add_argument("--note", default="Live mode requires explicit mic/VAD configuration before capture.")
    live.add_argument("--start", action="store_true")
    live.add_argument("--status", default=None)
    live.add_argument("--stop", default=None)
    live.add_argument("--ring-seconds", type=float, default=60.0)
    live.add_argument("--vad-threshold", type=float, default=-45.0)
    live.add_argument("--server", default=argparse.SUPPRESS)
    live.add_argument("--repo", default=argparse.SUPPRESS)

    background = sub.add_parser("background", help="Inspect and control the hmm background runtime.")
    background.add_argument("action", nargs="?", default="status", choices=["status", "pause", "resume", "capture"])
    background.add_argument("--session-id", default=None)
    background.add_argument("--seconds", type=float, default=None)
    background.add_argument("--route-preset", default=None)
    background.add_argument("--remember", action="store_true")
    add_common_options(background)

    memory = sub.add_parser("memory", help="Browse, search, export, or forget Akousmata traces.")
    memory.add_argument("action", nargs="?", default="list", choices=["list", "search", "export", "forget"])
    memory.add_argument("query", nargs="?")
    memory.add_argument("--trace-id", default=None)
    memory.add_argument("--limit", type=int, default=None)
    add_common_options(memory)

    bench = sub.add_parser("bench", help="Record report latency, server memory high-water, and output throughput over a folder.")
    bench.add_argument("folder")
    bench.add_argument("--limit", type=int, default=None)
    bench.add_argument("--output", default=None)
    add_common_options(bench)

    args = parser.parse_args()
    if not hasattr(args, "server"):
        args.server = DEFAULT_SERVER
    if not hasattr(args, "repo"):
        args.repo = DEFAULT_REPO
    if args.command_name == "listen":
        run_listen(args)
    elif args.command_name == "chat":
        run_chat(args)
    elif args.command_name == "sweep":
        run_sweep(args)
    elif args.command_name == "corpus-qa":
        run_corpus_qa(args)
    elif args.command_name == "live":
        run_live(args)
    elif args.command_name == "background":
        run_background(args)
    elif args.command_name == "memory":
        run_memory(args)
    elif args.command_name == "bench":
        run_bench(args)


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--server", default=argparse.SUPPRESS)
    parser.add_argument("--repo", default=argparse.SUPPRESS)


def run_listen(args: argparse.Namespace) -> None:
    report = post_json(args.server, "/report", {"path": args.path, "profile": "default"})
    command_output = build_command_output(report, command=args.command)
    if args.validate:
        loader = AkouoLoader()
        loader.validate("command-output", command_output)
    path = write_session(args.repo, args.path, report, command_output)
    print(str(path))


def run_chat(args: argparse.Namespace) -> None:
    if not args.question:
        raise SystemExit("aear chat requires at least one --question")
    session_id = args.session or default_session_id(args.path)
    if args.reset:
        path = session_path(args.repo, session_id)
        if path.exists():
            path.unlink()
    session = load_session(args.repo, session_id, args.path)
    report = post_json(args.server, "/report", {"path": args.path, "profile": "chat"})
    qa_items = []
    for question in args.question:
        answer = post_json(
            args.server,
            "/qa",
            {"path": args.path, "question": question, "thinking_budget": args.thinking, "context": context_text(session)},
        )
        append_turn(session, question, answer)
        qa_items.append(answer)
    session_json = save_session(args.repo, session)
    report["qa"] = [item.get("qa", item) for item in qa_items]
    command_output = build_command_output(report, command="/listen")
    path = Path(args.repo) / "journals" / f"{Path(args.path).stem}-chat.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    journal = render_journal(args.path, report, command_output)
    if qa_items:
        journal += "\n## Dialog\n\n"
        journal += f"- Session: `{session_id}`\n"
        journal += f"- Session JSON: `{session_json}`\n\n"
        for item in qa_items:
            qa = item.get("qa", item)
            journal += f"### {qa.get('question', '')}\n\n{qa.get('answer', '')}\n\n"
    path.write_text(journal, encoding="utf-8")
    write_json(path.with_suffix(".report.json"), report)
    print(str(path))


def run_sweep(args: argparse.Namespace) -> None:
    folder = Path(args.folder).expanduser().resolve()
    files = [path for path in sorted(folder.rglob("*")) if path.suffix.lower() in AUDIO_EXTS]
    if args.limit is not None:
        files = files[: args.limit]
    lexicon_path = Path(args.repo) / "lexicon" / f"{folder.name or 'sweep'}.jsonl"
    if lexicon_path.exists():
        lexicon_path.unlink()
    timeline_entries = []
    for audio_file in files:
        report = post_json(args.server, "/report", {"path": str(audio_file), "profile": "sweep"})
        command_output = build_command_output(report, command="/listen")
        claims = command_output.get("claim_summary", {})
        entry = {
            "path": str(audio_file),
            "duration_s": report.get("source", {}).get("duration_s"),
            "caption": report.get("caption", {}).get("brief") or report.get("caption", {}).get("dense"),
            "heard": [claim["statement"] for claim in claims.get("heard", [])[:5]],
            "measured": [claim["statement"] for claim in claims.get("measured", [])[:5]],
            "undetermined": [claim["statement"] for claim in claims.get("undetermined", [])[:5]],
        }
        append_lexicon_entry(lexicon_path, entry)
        timeline_entries.append(timeline_entry(audio_file, report))
    timeline_path = timeline_path_for_folder(args.repo, folder)
    write_timeline(timeline_path, timeline_entries)
    print(str(lexicon_path))


def run_corpus_qa(args: argparse.Namespace) -> None:
    target = Path(args.target).expanduser()
    timeline_path = target if target.is_file() else timeline_path_for_folder(args.repo, target)
    result = answer_timeline_question(load_timeline(timeline_path), args.question, max_results=args.max_results)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def run_live(args: argparse.Namespace) -> None:
    if args.start:
        result = post_json(args.server, "/live/start", {"ring_seconds": args.ring_seconds, "vad_threshold_dbfs": args.vad_threshold})
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        return
    if args.status:
        result = post_json(args.server, "/live/status", {"session_id": args.status})
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        return
    if args.stop:
        result = post_json(args.server, "/live/stop", {"session_id": args.stop})
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        return
    note = {
        "mode": "live",
        "enabled": True,
        "reason": args.note,
        "guardrails": [
            "Visible listening indicator required.",
            "Local disk only.",
            "Forensic-grade claims disabled by default.",
            "Critical-political-listening must be part of live routes.",
        ],
        "controls": {
            "start": "aear live --start",
            "status": "aear live --status <session_id>",
            "stop": "aear live --stop <session_id>",
        },
    }
    sys.stdout.write(json.dumps(note, indent=2) + "\n")


def run_background(args: argparse.Namespace) -> None:
    if args.action == "status":
        result = get_json(args.server, "/background/status")
    elif args.action == "pause":
        result = post_json(args.server, "/background/pause", {})
    elif args.action == "resume":
        result = post_json(args.server, "/background/resume", {})
    else:
        payload = {
            "session_id": args.session_id,
            "seconds": args.seconds,
            "route_preset": args.route_preset,
            "remember": args.remember,
        }
        result = post_json(args.server, "/background/capture", payload)
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")


def run_memory(args: argparse.Namespace) -> None:
    if args.action == "forget":
        if not args.trace_id and not args.query:
            raise SystemExit("hmm memory forget requires --trace-id or a trace id argument")
        result = post_json(args.server, "/memory/forget", {"trace_id": args.trace_id or args.query})
    else:
        params: dict[str, object] = {}
        if args.action == "search" and args.query:
            params["q"] = args.query
        if args.limit:
            params["limit"] = args.limit
        endpoint = "/memory/export" if args.action == "export" else "/memory"
        query = urlencode(params)
        result = get_json(args.server, f"{endpoint}?{query}" if query else endpoint)
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")


def run_bench(args: argparse.Namespace) -> None:
    result = run_report_benchmark(server=args.server, folder=args.folder, limit=args.limit, output=args.output)
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
