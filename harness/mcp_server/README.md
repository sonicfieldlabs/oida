# OIDA MCP Server

The stdio implementation lives in `harness/mcp_server`. This directory
preserves the plan's `harness/mcp-server/` shape.

It exposes report, transcription, QA, process metrics, and live-session
start/status/stop tools against the local OIDA daemon.

Run:

```bash
uv run python -m harness.mcp_server.server
```
