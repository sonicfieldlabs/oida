# Native listening and apertures

The opt-in `POST /sources/agent-native` owner route performs bounded file DSP
without a reasoning or audio-model provider. Supply `operation_id`, `path`, exact
`source_sha256`, and `permission_ref`. `mode` selects `centaur`, `human_reference`
or `beyond`; optional `bands_hz` keeps the requested disjoint bands explicit.
Non-audio inputs are not admitted. Unsupported requests return a refused aperture
decision rather than falling back to another mode.

The `agent-native` gateway preset uses the same route with `native_options` and
an explicit operation ID. Native reports are stored at `listening["akouo.agent-native"]`;
the canonical native-evidence extension keeps clocks, source and analysis registers,
per-window measurements, claim/pass references and derived-from relations separate.
Claims describe digital measurements, never physical ultrasonic capture or hearing.

`memory` defaults to `none`. `record` retains bounded summary measurements and
omission reasons, with no numeric arrays or audio. `record_audio` additionally
requires `derivatives_permitted: true` and an explicit future Unix `expires_at`
from the audio-retention policy. The active covenant is checked before analysis
and publication; incognito refuses persistence. Retained input must be WAV.
Akousmata publishes verified content-addressed objects and the record/grant through
one publication transaction. Derivatives inherit expiry and forgetting and have
no implicit public-disclosure permission.

Pyramid limits are 60 seconds, 192 kHz, two channels, 24 million scalar samples,
96 MiB source/decoded input, 256 MiB estimated working allocations, 2,048 frames
per level, FFT length 262,144, 64 MiB selected derivatives and a 30-second analysis
deadline. Levels are sequential and cancellable in chunks; over-budget levels
are explicitly omitted. Hann windows use SciPy's periodic recipe and magnitude
scaling. The long observation window runs once when admitted. Bin spacing is
reported independently of the observation-window duration.

The regression fixture combines 1 kHz and 40 kHz at 192 kHz. A 4:1 polyphase
conversion with Kaiser beta 8.6 removes 40 kHz; resampling back does not restore it.
The test compares retained high-band energy with a relative tolerance of 1e-6.
This is digital-processing evidence, not a calibrated acoustic experiment.

## Registered radio sources

`POST /sources/capture/register` takes a direct `url`, `consent: granted`,
`consent_ref`, `rights_ref`, `source_ref`, `retention: temp_only`, and optional
`max_seconds` (up to 60). List and delete via `/sources/capture/registered` and
`/sources/capture/registered/{id}`. IDs are normalized URL hashes; the registry
is separate from operator configuration, locked, atomically persisted and capped
at 64. Revocation cancels dependent active/queued work and is rechecked before
publication. Attribution alone does not grant retention.

Runtime streams use the Station public-address/DNS-pinning fetch rules at the
owner. Redirects are rechecked; private, local, link-local and Tailnet destinations,
credentials and file URLs are refused. Fetches are time/byte bounded. Only direct
MP3/AAC/WAV/FLAC/Ogg demuxing is admitted, from a local temporary download with
network protocols disabled in the decoder. Playlists and HLS remain unavailable.
Configured `oida/capture-sources/v2` entries can request the same behavior with
`network_policy: public_radio` and `retention: temp_only`; v1 radio entries retain
their trusted configured-source compatibility.

## System output and optional analysis

`GET /inputs/system-output` resolves the configured `OIDA_SYSTEM_OUTPUT_DEVICE`
against the actual AVFoundation inventory and a loopback-device identity.
`POST /inputs/system-output/start` uses the existing leased input/ring lifecycle;
no microphone substitution or driver installation occurs. Gateway admission
refuses retained system-output audio and requires temporary audio. Backend absence
is explicit. The capture host performs no monitor playback, avoiding feedback.

The Station exposes the backend check and start action in Mac inputs. Its input
session preserves `system_output` provenance through the gateway. Monitor chunk
delivery is refused at the owner, including requests made outside the UI.

`GET /sources/agent-native/capabilities` reports optional-worker availability.
Request `workers: ["nsgt", "kymatio"]` to select them; neither runs by default.
Each accepts 256–16,384 samples, up to two channels, in an isolated child with a
30-second deadline, 512 MiB RSS ceiling and 16 MiB output cap. Views inherit the
same audio-retention rules and total 64 MiB bundle cap. Failure produces an
omitted view with a reason. Startup removes abandoned worker temporaries while
preserving those owned by a live process.

Provision an isolated Python 3.12 environment with NumPy 1.26.4, SciPy 1.14.1,
NSGT 0.19 and Kymatio 0.3.0; install NumPy/setuptools/wheel before installing NSGT
with `--no-build-isolation`. Run the installer's `python -m listening_stack.spectral
--python /path/to/venv/bin/python --worker /path/to/oida/spectral_worker.py
--destination /path/to/spectral-workers.json`. It qualifies silence and stereo
tones at 48/96/192 kHz, records code/interpreter hashes and applies a guarded
NSGT integer-window compatibility fix. Set `OIDA_SPECTRAL_WORKERS_CONFIG` to that
receipt only when enabling these workers. No model or driver downloads occur at
runtime. NSGT uses the [upstream transform](https://github.com/grrrr/nsgt);
scattering uses the [Kymatio NumPy API](https://www.kymat.io/codereference.html).

Retained reports expose `/situated/decide` as the optional interpretation and
next-action path. It uses the configured provider and existing disclosure,
covenant and action gates, seeing bounded measurement text with claim references
and no raw audio or numeric arrays. Native measurements remain immutable; model
interpretation cannot create measurement evidence. Gateway `remember` and
`retain_library_audio` flags must match `native_options.memory`. Named covenant
overrides and caller-supplied evidence are rejected; this route uses the active
owner covenant and a retained permission reference and policy snapshot.
