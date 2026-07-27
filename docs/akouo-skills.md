# AKOUO Skill Manifests

`oida` exposes AKOÚŌ listening skills as explicit manifests. A skill is not a
model by itself. It is a routed listening position that tells the daemon,
dashboard, memory layer, and future desktop shell how to frame one audio segment.

Built-in manifests live in `oida/akouo_skills.py` and are exposed by:

```bash
curl -sS http://127.0.0.1:8765/akouo/skills
curl -sS http://127.0.0.1:8765/akouo/schema
```

## Skill Fields

Each `ListeningSkillManifest` has:

- `id`: stable lowercase identifier such as `signal-health`.
- `name`: reader-facing name.
- `version`: skill contract version.
- `description`: what the skill listens for.
- `listening_mode`: one of the schema modes, such as `basic`, `signal`,
  `spectral`, `ecological`, `music`, `speech`, `comparative`, `generative`, or
  `experimental`.
- `input_requirements`: duration, stream/file, stereo, or sample-rate hints.
- `model_requirements`: adapters required by the skill, such as `moss-audio`,
  `oida-dsp`, `akouo`, or `akousmata`.
- `memory_policy`: `none`, `read`, `write`, or `read_write`.
- `output_schema`: optional future structured-output contract.
- `ui_card`: dashboard card renderer hint.
- `enabled_by_default`: whether the skill should appear active by default in
  contributor-facing route presets.

## Presets

A `RoutePreset` names a reusable chain of skills:

- `basic`: first pass with general listening, spectral facts, and signal health.
- `field`: field/soundscape route.
- `signal`: technical diagnostics.
- `music`: musicological route.
- `voice`: speech route without making speech the product center.
- `recall`: read-only local Akousmata comparison.
- `remember`: comparison plus an explicit durable memory request.
- `extended-spectrum`: DSP-first caution route for high/low-frequency claims.
- `generative`: future bridge from listening observations to transformation
  prompts. It does not generate audio.

Requests may override the preset skill chain with `enabled_skill_ids`:

```json
{
  "path": "clip.wav",
  "route_preset": "basic",
  "enabled_skill_ids": ["signal-health", "spectral-cartographer"]
}
```

The daemon rejects unknown skills and rejects an empty active skill chain.

## Adding A Skill

1. Add a `ListeningSkillManifest` entry to `SKILLS` in `oida/akouo_skills.py`.
2. Add it to one or more `RoutePreset.skill_ids` entries, or create a new preset.
3. Keep claims grounded in MOSS and DSP limits. MOSS-Audio receives 16 kHz mono
   input; do not claim unsupported stereo image, ultrasonic content, or absolute
   physical level without capture-chain or DSP evidence.
4. Emit one attributable listening pass per hearing and preserve route
   decisions, source/cut/corpus provenance, disagreements, and unknowns. Prompt,
   transcript, caption, and contextual-note inputs are not `heard` evidence.
5. Run:

```bash
uv run python -m unittest discover -s tests
node --check oida/static/app.js
```

The dashboard skill manager loads the manifest dynamically, so new skills appear
without hard-coded UI changes.
