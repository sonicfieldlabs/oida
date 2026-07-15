# Contributing to Oída

Oída is a public-alpha listening instrument and an open research release.
Contributions are welcome when they make its evidence, privacy, portability,
or listening practice more accountable.

## Begin Here

1. Open an issue before a large interface, schema, or dependency change.
2. Fork the repository and create a focused branch.
3. Install the model-free development environment:

   ```bash
   uv sync --extra dev
   ```

4. Add or update tests and public documentation with the change.
5. Run the checks below and submit a pull request that explains the evidence
   for the change and any privacy or compatibility effect.

No model weights are required for the test suite. Do not commit recordings,
weights, credentials, local paths, generated results, or private listening
history.

## Contracts to Preserve

- Raw audio never leaves the operator machine by default.
- Raw-audio retention and deletion remain explicit and testable.
- A listening is stored durably only through an explicit remember action.
- Model output is fallible perception evidence, not final truth.
- AKOÚŌ claim categories stay separate: heard, measured, inferred,
  interpreted, speculative, and undetermined.
- Host and cloud integrations remain disabled until the operator enables them.
- An integration must not write secrets or machine-specific paths into events,
  exports, fixtures, logs, or documentation.

## Scoped Contribution Opportunities

These are useful public-alpha contributions with a bounded review surface:

1. **Signal-route fixtures.** Add a small, license-safe synthetic fixture for
   speech-like, tonal, percussive, noise, or field material. Assert measurable
   properties and claim boundaries, not one subjective description.
2. **Platform installation report.** Exercise the model-free quick start on a
   currently unverified macOS or Linux configuration. Report the exact OS,
   CPU, Python, `uv`, and `ffmpeg` versions, then improve only the instructions
   supported by that result.
3. **Dashboard accessibility.** Audit one complete keyboard and screen-reader
   path from source selection through a finished listening. Include a
   reproducible test and preserve visible focus and semantic labels.
4. **Gateway contract fixture.** Add a host-perception or provider fixture that
   proves provenance, evidence categories, retention, and failure fallback
   remain intact. No live account or network call may be required in CI.

An issue should state the intended opportunity, acceptance criteria, and which
contract it touches. Small documentation fixes do not need prior approval.

## Local Checks

```bash
uv run pytest -q
uv run python -m compileall -q oida harness bench_adapter
node --check oida/static/app.js
```

Run the repository check before proposing a release-facing change:

```bash
scripts/run_local_checks.sh
scripts/run_local_checks.sh release
```

The release check also builds the macOS shell and performs an isolated stub
daemon smoke test, so it takes longer and requires the relevant Apple tools.

## Dependencies and Documentation

Keep runtime dependencies small. Network-capable packages belong in an
optional path unless the local daemon itself needs them. Update the API or
gateway documentation when a route, schema, default, or lifecycle changes.
Update [models and licensing](docs/models-and-licensing.md) when adding a model
or provider surface.

Contributions are provided under the repository's Apache-2.0 license. By
submitting a contribution, you confirm that you have the right to provide it
and that any included fixture or media has a documented compatible license.
