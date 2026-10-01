# Oída for Pi (local candidate)

`oida integrate pi` stages a native Pi extension and returns its explicit launch
command. It does not modify Pi settings, install globally, read authentication,
start a model or send a message. Start Oída separately, then launch Pi with the
returned `--extension` path. The selected loopback origin is stored beside the
extension. Pi owns provider credentials and conversation retention.

Three native tools reuse `/gateway/capabilities`, `/gateway/listen` and operation receipts:
capabilities, permission-qualified session listening, and receipt/cancellation.
Choose a stable `operation_id` for each request. Exact retries use the same ID;
changed payloads must use a new ID. The owner refuses duplicate dispatch with an
existing receipt; the adapter returns that receipt without claiming result replay
or payload equivalence. A timeout/cancellation is unconfirmed execution,
never permission to retry automatically. Inspect the receipt before reconciliation.
The owner covenant remains authoritative. Raw reports returned to Pi enter the
host conversation; Oída ephemeral processing does not erase Pi's own history.

The adapter targets the locally verified Pi 0.85.1 extension interface. Existing
Hermes, Claude Code, Codex, OpenCode and OpenClaw adapters retain their independent
host mechanisms. Adapter conformance and stub owner tests do not establish live
provider quality or every host's authenticated UI behavior.
