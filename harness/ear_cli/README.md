# ear CLI

The installable CLI lives in `harness/ear_cli` because Python packages cannot
contain a hyphen. This directory preserves the plan's `harness/ear-cli/` shape.

Use:

```bash
uv run ear report clip.wav
uv run ear qa clip.wav "what closes at 0:42?" --thinking 512
uv run ear transcribe --ts sentence clip.wav
```

