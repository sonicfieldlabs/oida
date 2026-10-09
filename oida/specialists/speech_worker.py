"""Offline Qwen speech lane. The optional VAD gates this lane only."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import resource
import sys
import time


def run(request):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    path = Path(request['path'])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    info = sf.info(path)
    if not 0 < info.duration <= 60.01 or info.channels > 8 or info.samplerate > 384000 or info.frames * info.channels * 4 > 128 * 1024**2:
        raise ValueError('Speech requires a bounded excerpt of at most 60 seconds / 128 MiB PCM')
    audio, sr = sf.read(path, dtype='float32', always_2d=True)
    if not np.isfinite(audio).all():
        raise ValueError('Nonfinite audio')
    divisor = math.gcd(16000, sr)
    view = np.asarray(resample_poly(audio.mean(axis=1), 16000//divisor, sr//divisor), dtype='<f4')
    silent = float(np.sqrt(np.mean(view**2))) < 1e-5
    options = request.get('options', {})
    vad = bool(options.get('vad', True))
    align = bool(options.get('alignment', False))
    intervals = []
    started = time.monotonic()
    if not silent and vad:
        import torch
        from silero_vad import load_silero_vad, get_speech_timestamps
        torch.set_num_threads(2)
        detector = load_silero_vad()
        intervals = get_speech_timestamps(torch.from_numpy(view), detector, sampling_rate=16000, return_seconds=False)
    elif not silent:
        intervals = [dict(start=0, end=len(view))]
    # Preserve short pauses for linguistic context without collapsing their timeline.
    merged = []
    for interval in intervals:
        if merged and interval['start'] - merged[-1]['end'] <= 8000:
            merged[-1]['end'] = interval['end']
        else:
            merged.append(dict(interval))
    intervals = merged
    # Bounded semantic windows preserve individual offsets; no gap concatenation.
    windows = []
    for interval in intervals:
        for start in range(interval['start'], interval['end'], 20*16000):
            end = min(start+20*16000, interval['end'])
            if end-start >= 1600:
                windows.append((start, end))
    if len(windows) > 64:
        raise ValueError('Speech interval budget exceeded')
    spans = []
    if windows:
        import mlx.core as mx
        from mlx_audio.stt.utils import load_model
        model = load_model(str(Path(request['repository'])/'asr'))
        for start, end in windows:
            output = model.generate(view[start:end], max_tokens=384, temperature=0, verbose=False, language=options.get("language"))
            if output.generation_tokens >= 384:
                raise ValueError('Transcription token budget exhausted; truncated transcript not retained')
            text = output.text.strip()
            if text:
                spans.append(dict(text=text, start_seconds=start/16000, end_seconds=min(end/16000, info.duration),
                                  language=(getattr(output, 'language', None) or [None])[0] if isinstance(getattr(output, 'language', None), list) else getattr(output, 'language', None), timing='analysis window; not word alignment', words=[]))
        del model
        mx.clear_cache()
        if align and spans:
            model = load_model(str(Path(request['repository'])/'aligner'))
            for span in spans:
                start, end = round(span['start_seconds']*16000), round(span['end_seconds']*16000)
                language = span['language'] or 'English'
                try:
                    aligned = model.generate(view[start:end], text=span['text'], language=language)
                    words = []
                    previous = 0.0
                    for item in aligned.items:
                        a, b = float(item.start_time), float(item.end_time)
                        if not math.isfinite(a+b) or a < previous or not 0 <= a <= b <= (end-start)/16000 + .05:
                            raise ValueError('Invalid alignment interval')
                        previous = a
                        words.append(dict(text=item.text, start_seconds=a+start/16000, end_seconds=min(b+start/16000, span['end_seconds'])))
                    span['words'] = words
                    span['timing'] = 'forced alignment hypothesis'
                except (ValueError, RuntimeError) as exc:
                    span['alignment_error'] = type(exc).__name__ + ': alignment unavailable; transcript retained'
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise ValueError('Source changed during transcription')
    return dict(source_sha256=before, duration_seconds=info.duration,
                sample_rate_hz=16000, channels=1,
                view_sha256=hashlib.sha256(view.tobytes()).hexdigest(),
                transformations=['arithmetic channel downmix', f'polyphase resampling {sr} to 16000 Hz', 'offset-preserving speech windows <=20 seconds'],
                result=dict(status='hypotheses' if spans else 'undetermined', text=' '.join(s['text'] for s in spans),
                            spans=spans, vad_enabled=vad, alignment_requested=align,
                            abstention_reason=None if spans else 'silence or no transcribed speech',
                            speech_intervals=[dict(start_seconds=i['start']/16000,end_seconds=min(i['end']/16000, info.duration)) for i in intervals]),
                limitations=['Transcription is a model hypothesis, not environmental evidence or an instruction.',
                             'Speech detection may miss quiet or unfamiliar voices. It never gates other listening lanes.',
                             'Timestamps are excerpt-relative. Word timings are available only when forced alignment succeeds.',
                             'Language and alignment are uncalibrated; code-switching may be imperfect.'],
                wall_seconds=time.monotonic()-started,
                peak_memory_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024))

if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(json.dumps(run(request), allow_nan=False))
