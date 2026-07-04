from __future__ import annotations

import sys


HARNESS_COMMANDS = {"listen", "chat", "sweep", "corpus-qa", "live", "background", "memory", "bench"}
SERVER_FLAGS = {"--profile", "--host", "--port"}
HELP = """usage: oida [serve options] | oida serve [options] | oida <command> [options]

oida local listening daemon and AKOUO harness.

daemon:
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

Legacy `oida` command aliases are still installed for existing scripts.
"""


def main() -> None:
    args = sys.argv[1:]
    if args in (["-h"], ["--help"]):
        print(HELP)
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


if __name__ == "__main__":
    main()
