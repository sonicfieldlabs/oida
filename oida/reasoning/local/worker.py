"""One offline, bounded text planning operation. No tools, downloads or shell."""

import json
from pathlib import Path
import resource
import sys
import time


def run(request):
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    import mlx.core as mx

    model, tokenizer = load(request["path"])
    messages = request["messages"]
    schema = request["schema"]
    messages = [dict(m) for m in messages]

    def inline(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(schema["$defs"][value["$ref"].split("/")[-1]])
            return {
                k: inline(v)
                for k, v in value.items()
                if k not in {"$defs", "title", "default"}
            }
        return [inline(v) for v in value] if isinstance(value, list) else value

    prompt_schema = inline(schema)
    messages[0]["content"] += (
        "\nOutput keys: summary, findings (array), next_move (object). Evidence refs must be copied literally from evidence[].ref. A segment uses start_seconds and duration seconds, not end time. Do not copy request IDs as evidence refs. /no_think"
    )
    messages[0]["content"] += (
        "\nReturn one JSON object matching this schema, without markdown or deliberation:\n"
        + json.dumps(prompt_schema)
    )
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    token_count = len(tokenizer.encode(prompt))
    if token_count > 8192:
        raise ValueError("Input exceeds the validated 8192-token planning context")
    started = time.monotonic()
    text = ""
    count = 0
    value = None
    for response in stream_generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=request["max_tokens"],
        sampler=make_sampler(temp=0),
    ):
        text += response.text
        count += 1
        try:
            candidate, _ = json.JSONDecoder().raw_decode(text.lstrip())
            if isinstance(candidate, dict):
                value = candidate
                break  # Stop at the completed structured response; no trailing prose.
        except json.JSONDecodeError:
            pass
    if value is None and count >= request["max_tokens"]:
        raise ValueError("Planning output token budget exhausted")
    # Reasoning traces are never retained. Non-thinking output must be a JSON object.
    if value is None:
        raise ValueError("No complete JSON planning object")
    import jsonschema

    jsonschema.validate(value, schema)
    return dict(
        content=json.dumps(value, ensure_ascii=False),
        usage=dict(
            prompt_tokens=token_count,
            completion_tokens=count,
            total_tokens=token_count + count,
        ),
        seconds=time.monotonic() - started,
        peak_memory_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        / (1024**2 if sys.platform == "darwin" else 1024),
        metal_peak_mib=mx.get_peak_memory() / 1024**2,
    )


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(
        json.dumps(run(json.loads(Path(sys.argv[1]).read_text())), allow_nan=False)
    )
