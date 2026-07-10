# Oída for Claude Code

The local Claude Code plugin contributes one listening skill, an /oida
command, and the Oída stdio MCP server. The MCP process ensures the singleton
local gateway before connecting.

Validate it with:

    claude plugin validate /path/to/oida/integrations/claude

Use oida integrate claude to register the same MCP server and install the
skill locally without a cloud service.
