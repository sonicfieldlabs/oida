# Contributing

`hmm` is a local-first listening agent. Changes should preserve four contracts:

- Raw audio never leaves the operator machine by default.
- Raw audio retention and deletion paths stay explicit and testable.
- MOSS-Audio output is perception evidence, not final truth.
- AKOUO claim categories remain separate: heard, measured, inferred,
  interpreted, speculative, undetermined.

## Local Checks

```bash
uv sync --extra dev
uv run python -m unittest discover -s tests
uv run pytest
```

For release smoke checks, start the stub daemon and run:

```bash
scripts/run_local_checks.sh release
```

## Dependencies

Keep runtime dependencies small. Network-capable packages should be dev-only
unless the daemon itself needs them at runtime.
