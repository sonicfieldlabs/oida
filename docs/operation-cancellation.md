# Owner operation cancellation (unreleased)

Configured-source jobs and synchronous capture use their existing acquisition ID
and `/sources/acquisitions/{id}/cancel`. Cancellation is durable and repeatable.
Capture is interrupted through its existing supervisor. During listening, an
accepted cancellation prevents the completed model result from reaching event
publication, background history or canonical record storage. Current source policy
is checked again after inference. An already-computing model may continue until it
returns; this is cooperative cancellation, not GPU-kernel termination.

Gateway clients opt in with `operation_id` on `POST /gateway/listen`. Upload clients
supply an `operation_id` form field on `POST /upload`. Inspect
`GET /operations/{id}` and cancel with `POST /operations/{id}/cancel`. These routes
inherit the existing owner authentication and loopback protections. IDs contain
1–80 ASCII letters, digits, underscores or hyphens. Reusing an ID returns HTTP 409
with its existing receipt and does not repeat work. Use a fresh ID for an intentional
new attempt. Existing clients without an ID retain their previous behavior.

The upload operation begins after the framework parses multipart input. It checks
cancellation between file-copy chunks and around normalization; cancellation removes
raw and normalized temporary files. Disconnecting a transport before that point is
not an acknowledged owner cancellation. An active normalizer may finish before its
output is discarded. A completed upload is not undone by cancelling a later listen;
its existing raw-audio retention policy still applies.

A locked commit fence changes the receipt to `committing` immediately before the
first publication side effect. Cancellation after this boundary returns false; it
never promises to retract a committed record. Restart marks unfinished operations
`interrupted` and never reruns them. Recovery does not infer whether a record commit
preceded a crash; inspect canonical references and reconcile through the existing
owner journal when needed. Journal and account commits remain separate transactions.

Operation receipts contain status, update time and successful event/record links,
not input paths, transcripts or exception text. Source receipts retain their existing
owner-only acquisition metadata. Graceful shutdown cancels queued, direct-capture
and uncommitted listening work. Queue expiry still bounds the start of listening,
not total model runtime.

Tests exercise owner API cancellation during inference and upload normalization,
actual successful gateway/record links, replayed IDs, worker failure, restart,
post-inference refusal, queue behavior and the commit fence. Synthetic capture and
stub execution do not establish native-device or model-kernel interruption.
