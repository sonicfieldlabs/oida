# Evidence limits: spectral access and agent exchange (unreleased)

Oída does not currently establish native understanding outside human hearing, nor
semantic negotiation between agents beyond the implemented exchange of records,
capabilities, requests and receipts. Neither is a first-release requirement. The
gateway advertises these limits explicitly. A valid protocol object is evidence of
an exchange, not agreement about meaning or successful negotiation of authority.

| Boundary | What the current system can record or test | Evidence needed for a stronger claim |
| --- | --- | --- |
| Sample capture range | Declared device configuration and apparatus references | Measured microphone/sensor response, analogue path, converter and calibration for the named setup |
| Sampled representation | Actual sample rate, channels, bytes and digital band measurements | Verified source lineage; sample rate alone cannot establish the original capture bandwidth |
| Model input range | Attributed model/preprocessor execution and declared input transformations | Measured effective input path for the exact model/checkpoint, preprocessing and inference configuration |
| Human perceptual access | A generated rendering and declared playback path | Actual playback conditions and listener evidence; an available file or browser control does not prove hearing |
| Interpretation | Attributed output, uncertainty and independent accounts | Task-specific evaluation that distinguishes detection, discrimination, description and inference |

Nyquist constrains a sampled representation; it does not certify that a physical
apparatus captured every frequency below it. A high-rate file can contain only an
ordinary-band source, synthetic content, filtering artefacts or unresolved noise.
Likewise, a model may resample that file or discard part of its representation.
A model's architectural input size, advertised rate or ability to open a file is
not evidence of useful sensitivity or interpretation across that range.

Frequency translation adds an offset to a filtered digital band. Pitch shifting
scales frequencies; this runtime adapter also changes duration. Playback-rate
change reinterprets a sample sequence at a new rate. Filtered resampling changes
the sampling grid while preserving the selected duration. These are distinct
transformations with distinct receipts. A rendered derivative may make a pattern
accessible through an ordinary-band playback or model path; that does not make
the listener or model natively sensitive to the original band, preserve every
original relation, or establish the pattern's physical cause.

Agent adapters currently bind host runtimes and transport inspectable requests,
capabilities and accounts. Receipt identity, schema validity and successful tool
discovery do not establish shared concepts, resolved disagreement, negotiated
permissions or collective understanding. Future claims of negotiation need a
specified protocol and task, independently attributable participants, explicit
proposals/acceptances/refusals, observable revisions, authority checks, failure and
restart behavior, and evaluated outcomes. No "first" or priority claim is needed.

Capability descriptions may advance only with evidence for a named configuration
and task. Retain the apparatus, sampled-representation, model-input, rendering,
listener and interpretation qualifications independently. Synthetic tests establish
software/DSP behavior; they are never promoted to physical capture, human access
or model-understanding evidence.
