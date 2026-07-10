# Oída for Codex

This local marketplace contains the installable Oída Codex plugin. It
connects to the stdio gateway command, so Codex reuses a running gateway or
starts one singleton process on first listening use.

    codex plugin marketplace add /path/to/oida/integrations/codex
    codex plugin add oida@oida-local

Restart Codex after installation. The plugin contributes the oida-listening
skill plus Oída MCP tools, resources, and prompts.
