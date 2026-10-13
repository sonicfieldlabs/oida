# Finite playlist acquisition

`oida/stream-formats/v1` declares an opt-in subset. Existing registrations retain `playlist_policy=refuse`; re-registering a source requires explicit finite admission. The source's rights/consent references remain required by the capture registry. Capability browsing performs no capture or auto-install.

Accepted new inputs are a single-entry M3U or a completed, unencrypted HLS media playlist containing MPEG-TS or packed AAC segments. Limits are 64 KiB per playlist, sixteen segments, sixty seconds declared duration, 32 MiB aggregate bytes and one 45-second acquisition deadline. Public destination validation occurs for every URL and redirect; segment resources and their final redirect targets must remain on the declared origin. HTTPS cannot downgrade. DNS resolution has a bounded wait and bounded outstanding resolver capacity.

Live HLS, master/nested playlists, encryption, byte ranges, fMP4 and PLS are refused. Content must be frozen locally before FFmpeg sees it; the decoder uses only file/pipe protocols and an admitted demuxer. Receipts bind playlist/segment hashes and byte counts using `oida/stream-acquisition/v1`. A generated decode test verifies native 48 kHz mono PCM output without claiming that all network streams preserve rate/channel identity. Cancellation, budgets, unsupported tags and redirects are covered separately.

The coordinator offers the finite option only after the exact owner declaration answers. The older broad HLS availability field remains unavailable: this subset is not blanket live-HLS support. Qualify each real source and its source permissions before enabling capture. No microphone, system audio, real station stream or external model was used to qualify this candidate.

`oida/aperture-window/v1` also binds native sampled preview intervals to one frozen source buffer. Requested intervals must be finite, ordered, nonnegative and within the file duration. Request identity includes source, interval, bands, mode and claim kind. This receipt does not declare acoustic-model window support; that remains an exact route/deployment/source qualification gate.
