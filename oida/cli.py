from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser


HARNESS_COMMANDS = {"listen", "chat", "sweep", "corpus-qa", "live", "background", "memory", "bench"}
SERVER_FLAGS = {"--profile", "--host", "--port"}
LIFECYCLE_COMMANDS = {"start", "stop", "status", "doctor", "agent", "gateway", "integrate"}
HELP = """usage: oida [serve options] | oida serve [options] | oida <command> [options]

oída unified local listening agent and gateway.

lifecycle and integrations:
  oida start [--profile mac-mps|stub]   Ensure one background gateway.
  oida agent [--library]                Open the agent or sonic library.
  oida gateway --stdio --ensure-daemon  MCP entrypoint for local agents.
  oida status | doctor | stop
  oida integrate hermes|codex|claude|openclaw|opencode|all

foreground daemon:
  oida --profile mac-mps --host 127.0.0.1 --port 8765
  oida serve --profile stub

harness commands:
  listen    Run one routed listening session.
  chat      Ask questions of one clip and append a journal.
  sweep     Batch reports over a folder and build a lexicon JSONL.
  corpus-qa Answer from a merged sweep timeline.
  live      Start, inspect, stop, or describe live local listening.
  background Inspect, pause/resume, or quick-capture from the background runtime.
  memory    Browse, search, export, or forget Akousmata traces.
  bench     Benchmark report latency, memory, and output throughput.

Legacy `hmm` and `aear` command aliases are still installed for existing scripts.
"""


def main() -> None:
    args = sys.argv[1:]
    if args in (["-h"], ["--help"]):
        print(HELP)
        return
    if args in (["-V"], ["--version"]):
        from oida import __version__

        print(__version__)
        return
    if args and args[0] in LIFECYCLE_COMMANDS:
        _lifecycle_main(args)
        return
    if args and args[0] in HARNESS_COMMANDS:
        from harness.akoe_cli import main as harness_main

        harness_main()
        return
    if args and args[0] == "serve":
        from oida.server import main as server_main

        sys.argv = [sys.argv[0], *args[1:]]
        server_main()
        return
    if any(arg in SERVER_FLAGS or arg.startswith("--profile=") or arg.startswith("--host=") or arg.startswith("--port=") for arg in args):
        from oida.server import main as server_main

        server_main()
        return
    if not args:
        from oida.server import main as server_main

        server_main()
        return
    from harness.akoe_cli import main as harness_main

    harness_main()


def _lifecycle_main(args: list[str]) -> None:
    command = args[0]
    rest = args[1:]
    if command == "start":
        parser = argparse.ArgumentParser(prog="oida start", description="Ensure the singleton local Oída gateway.")
        parser.add_argument("--profile", choices=["mac-mps", "cuda-server", "stub"], default=None)
        parser.add_argument("--json", action="store_true")
        ns = parser.parse_args(rest)
        from oida.lifecycle import ensure_gateway

        _print_result(ensure_gateway(profile=ns.profile), as_json=ns.json)
        return
    if command == "stop":
        parser = argparse.ArgumentParser(prog="oida stop", description="Stop a gateway managed by Oída.")
        parser.add_argument("--json", action="store_true")
        ns = parser.parse_args(rest)
        from oida.lifecycle import stop_gateway

        _print_result(stop_gateway(), as_json=ns.json)
        return
    if command == "status":
        parser = argparse.ArgumentParser(prog="oida status", description="Inspect the local Oída gateway.")
        parser.add_argument("--json", action="store_true")
        ns = parser.parse_args(rest)
        from oida.integrations import inspect_integrations
        from oida.lifecycle import gateway_status

        result = {**gateway_status(), "integrations": inspect_integrations()}
        _print_result(result, as_json=ns.json)
        return
    if command == "doctor":
        parser = argparse.ArgumentParser(prog="oida doctor", description="Check the complete listening stack and local hosts.")
        parser.add_argument("--json", action="store_true")
        ns = parser.parse_args(rest)
        from oida.integrations import inspect_integrations
        from oida.lifecycle import doctor

        result = doctor()
        result["integrations"] = inspect_integrations()
        _print_result(result, as_json=ns.json)
        return
    if command == "agent":
        parser = argparse.ArgumentParser(prog="oida agent", description="Open the Oída agent or Akousmata navigator.")
        parser.add_argument("--profile", choices=["mac-mps", "cuda-server", "stub"], default=None)
        parser.add_argument("--library", action="store_true")
        parser.add_argument("--no-open", action="store_true")
        parser.add_argument("--json", action="store_true")
        ns = parser.parse_args(rest)
        from oida.lifecycle import ensure_gateway

        status = ensure_gateway(profile=ns.profile)
        url = str(status["url"]).rstrip("/") + ("/library/" if ns.library else "/")
        opened = False if ns.no_open else webbrowser.open(url)
        _print_result({"running": True, "url": url, "opened": opened}, as_json=ns.json)
        return
    if command == "gateway":
        parser = argparse.ArgumentParser(prog="oida gateway", description="Expose the local agentic listening gateway.")
        parser.add_argument("--stdio", action="store_true", help="Run the Oída MCP server over stdio.")
        parser.add_argument("--ensure-daemon", action="store_true", help="Ensure the REST/UI gateway before connecting.")
        parser.add_argument("--profile", choices=["mac-mps", "cuda-server", "stub"], default=None)
        parser.add_argument("--json", action="store_true")
        ns = parser.parse_args(rest)
        if ns.ensure_daemon or ns.stdio:
            from oida.lifecycle import ensure_gateway

            ensure_gateway(profile=ns.profile)
        if ns.stdio:
            os.environ["OIDA_MCP_ENSURE_DAEMON"] = "0"
            from oida.mcp_server import main as mcp_main

            mcp_main()
            return
        from oida.lifecycle import gateway_status

        result = gateway_status()
        result["transports"] = {"rest": "/gateway/*", "mcp_http": "/mcp", "mcp_stdio": "oida gateway --stdio --ensure-daemon"}
        _print_result(result, as_json=ns.json)
        return
    if command == "integrate":
        parser = argparse.ArgumentParser(prog="oida integrate", description="Install a local Oída host adapter.")
        parser.add_argument("target", choices=["hermes", "codex", "claude", "openclaw", "opencode", "all"])
        parser.add_argument("--json", action="store_true")
        ns = parser.parse_args(rest)
        from oida.integrations import install

        _print_result(install(ns.target), as_json=ns.json)
        return
    raise ValueError(f"unknown Oída lifecycle command: {command}")


def _print_result(value: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                print(f"{key}: {json.dumps(item, ensure_ascii=False, default=str)}")
            else:
                print(f"{key}: {item}")
        return
    print(value)


if __name__ == "__main__":
    main()
