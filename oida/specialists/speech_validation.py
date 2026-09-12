"""Task-specific validation on top of Earworm's generic evidence envelope."""
import math


def validate_transcript(result, duration):
    if result.get('status') not in {'hypotheses', 'undetermined'}:
        raise ValueError('Invalid transcript status')
    spans = result.get('spans')
    if not isinstance(spans, list) or len(spans) > 64:
        raise ValueError('Invalid transcript span count')
    if not isinstance(result.get('text'), str) or len(result['text']) > 48000:
        raise ValueError('Invalid transcript text')
    previous = 0.0
    for span in spans:
        start, end = span['start_seconds'], span['end_seconds']
        if not math.isfinite(start+end) or not 0 <= previous <= start < end <= duration + .0001:
            raise ValueError('Transcript interval outside excerpt or overlapping')
        previous = end
        if not isinstance(span.get('text'), str) or not span['text'].strip():
            raise ValueError('Empty transcript span')
        last = start
        words = span.get('words', [])
        if not isinstance(words, list) or len(words) > 1024:
            raise ValueError('Invalid aligned word count')
        for word in words:
            a, b = word['start_seconds'], word['end_seconds']
            if not math.isfinite(a+b) or not start <= last <= a <= b <= end+.0001:
                raise ValueError('Aligned word outside speech span')
            last = a
            if not isinstance(word.get('text'), str):
                raise ValueError('Invalid word text')
    if result['text'] != ' '.join(s['text'] for s in spans):
        raise ValueError('Transcript differs from its evidence spans')
    if (result['status'] == 'undetermined') != (not spans):
        raise ValueError('Abstention contradicts transcript')
