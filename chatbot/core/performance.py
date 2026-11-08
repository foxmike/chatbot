"""Bounded trailing performance protocol; dialogue is streamed independently."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
import re
import time
from typing import Literal

MAX_METADATA_CHARS = 240
MAX_TRAILERS = 6
MAX_SEGMENTS = 64
GRACE_SECONDS = .3


@dataclass(frozen=True, slots=True)
class Performance:
    emotion: tuple[str, ...] = ()
    intensity: Literal['low', 'mid', 'high'] | None = None
    tone: str | None = None
    style: str | None = None
    pace: Literal['slow', 'fast'] | None = None
    emphasis: tuple[str, ...] = ()
    pause: Literal['short', 'long'] | None = None


@dataclass(frozen=True, slots=True)
class DialogueSegment:
    index: int
    text: str
    performance: Performance | None = None


@dataclass(frozen=True, slots=True)
class Dialogue:
    segments: tuple[DialogueSegment, ...]
    visible_text: str | None = None

    @property
    def text(self) -> str:
        return self.visible_text if self.visible_text is not None else '\n'.join(s.text for s in self.segments)


@dataclass(frozen=True, slots=True)
class PerformanceCapabilities:
    controls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Delta:
    text: str
    segment_index: int


@dataclass(frozen=True, slots=True)
class SegmentComplete:
    segment: DialogueSegment


@dataclass(frozen=True, slots=True)
class Dropped:
    reason: str


def validate_performance(raw, text: str):
    """Validate fields independently, keeping valid siblings and recording failures."""
    errors = []
    if not isinstance(raw, dict):
        return None, ['not_object']
    values = {}
    names = {'emo', 'int', 'tone', 'style', 'pace', 'emph', 'pause'}
    errors.extend('unknown_field' for key in raw if key not in names)
    for short, name, size in [('tone', 'tone', 40), ('style', 'style', 40)]:
        if short in raw:
            value = raw[short]
            if isinstance(value, str) and 0 < len(value) <= size and not any(c in value for c in '\r\n'):
                values[name] = value
            else:
                errors.append('bad_' + short)
    for short, name, options in [('int', 'intensity', ('low', 'mid', 'high')),
                                  ('pace', 'pace', ('slow', 'fast')),
                                  ('pause', 'pause', ('short', 'long'))]:
        if short in raw:
            if isinstance(raw[short], str) and raw[short] in options:
                values[name] = raw[short]
            else:
                errors.append('bad_' + short)
    for short, name, count, size in [('emo', 'emotion', 2, 24), ('emph', 'emphasis', 3, 40)]:
        if short not in raw:
            continue
        value = raw[short]
        valid = []
        if not isinstance(value, list):
            errors.append('bad_' + short)
            continue
        if len(value) > count:
            errors.append('over_' + short)
        for item in value[:count]:
            if (isinstance(item, str) and 0 < len(item) <= size and '\n' not in item
                    and '\r' not in item and (short != 'emo' or len(item.split()) <= 3)
                    and (short != 'emph' or (len(item.split()) == 1 and re.search(r'(?<!\w)' + re.escape(item) + r'(?!\w)', text)))):
                valid.append(item)
            else:
                errors.append('bad_' + short)
        values[name] = tuple(valid)
    if not values.get('emotion'):
        values.pop('intensity', None)
    result = Performance(**values)
    return (result if any(asdict(result).values()) else None), errors


def performance_prompt_block(caps: PerformanceCapabilities, *, debug: bool = False) -> str:
    controls = set(caps.controls)
    keys = {'emotion': 'emo', 'intensity': 'int', 'tone': 'tone', 'style': 'style',
            'pace': 'pace', 'emphasis': 'emph', 'pause': 'pause'}
    fields = ', '.join(v for k, v in keys.items() if debug or k in controls or (k == 'intensity' and 'emotion' in controls))
    if not fields:
        return ''
    return (
        'Your reply is spoken aloud. Keep plain dialogue and its paragraph breaks. '
        'After a dialogue line you may add a separate line starting @@ followed by one JSON object. '
        f'Use only these optional keys: {fields}. '
        'emo: up to two emotion labels (24 chars, 3 words each); int: low/mid/high; '
        'tone/style: up to 40 chars; pace: slow/fast; emph: up to three single words '
        'copied exactly from that dialogue; pause: short/long. '
        'Choose delivery naturally in character; plain delivery needs no metadata. '
        'At most 6 metadata lines, each at most 240 characters. Never speak or explain metadata. '
        'To discuss literal @@ JSON, quote it rather than starting a line with it.'
    )


class DialogueStreamParser:
    """Emit normal text immediately, withholding only a possible line-start trailer.

    Paragraph breaks are retained in visible deltas. Metadata has a reserved
    line-start ``@@ {`` signature; quoted or inline markers remain literal.
    No asynchronous task is owned by the parser.
    """
    def __init__(self):
        self.mode = 'start'
        self.probe = ''
        self.line = ''
        self.line_at = None
        self.pending = None
        self.pending_at = None
        self.separator = ''
        self.emitted = False
        self.index = 0
        self.trailers = 0
        self.segments = []
        self.dropped = Counter()
        self.closed = False
        self.oversized = False
        self.waits = []

    def _drop(self, reason, out):
        self.dropped[reason] += 1
        out.append(Dropped(reason))

    def _release(self, out, performance=None):
        if self.pending is None:
            return
        segment = DialogueSegment(self.index, self.pending, performance)
        self.index += 1
        if len(self.segments) < MAX_SEGMENTS:
            self.segments.append(segment)
        else:
            self._drop('segment_limit', out)
        out.append(SegmentComplete(segment))
        if self.pending_at is not None and len(self.waits) < MAX_SEGMENTS:
            self.waits.append((time.monotonic() - self.pending_at) * 1000)
        self.pending = None
        self.pending_at = None

    def _text(self, text, out):
        if not text:
            return
        self._release(out)
        if not self.line:
            if self.emitted and self.separator:
                out.append(Delta(self.separator, self.index))
            self.separator = ''
        out.append(Delta(text, self.index))
        self.emitted = True
        if self.line_at is None:
            self.line_at = time.monotonic()
        if len(self.line) + len(text) <= 8192:
            self.line += text
        else:
            # Release bounded plain segments of exceptionally long lines.
            self.pending = self.line
            self.line = ''
            self._release(out)
            self.line = text
            self._drop('long_dialogue_line', out)

    def _newline(self, out):
        if self.line.strip():
            self.pending = self.line.strip()
            self.pending_at = time.monotonic()
        self.line = ''
        self.line_at = None
        self.separator = (self.separator + '\n')[:2]
        self.mode = 'start'

    def _metadata(self, out):
        self.trailers += 1
        if self.oversized:
            self._drop('too_long', out)
        elif self.trailers > MAX_TRAILERS:
            self._drop('over_limit', out)
        elif self.pending is None:
            self._drop('late' if self.index else 'orphan', out)
        else:
            try:
                raw = json.loads(self.probe[3:])
            except (ValueError, TypeError):
                self._drop('bad_json', out)
            else:
                performance, errors = validate_performance(raw, self.pending)
                for reason in errors:
                    self._drop(reason, out)
                self._release(out, performance)
        self._release(out)
        self.probe = ''
        self.oversized = False
        self.mode = 'start'

    def feed(self, text: str):
        if self.closed:
            return []
        out = []
        for char in text:
            if self.mode == 'metadata':
                if char == '\n':
                    self._metadata(out)
                elif len(self.probe) < MAX_METADATA_CHARS:
                    self.probe += char
                else:
                    self.oversized = True
                continue
            if self.mode == 'start':
                if char == '@':
                    self.probe = '@'
                    self.mode = 'probe'
                    continue
                if char == '\n':
                    self.separator = (self.separator + '\n')[:2]
                    continue
                self.mode = 'text'
            elif self.mode == 'probe':
                self.probe += char
                if self.probe in ('@', '@@', '@@ '):
                    continue
                if self.probe in ('@@ {', '@@ ['):
                    self.mode = 'metadata'
                    continue
                value, self.probe = self.probe, ''
                self.mode = 'text'
                if value.endswith('\n'):
                    self._text(value[:-1], out)
                    self._newline(out)
                else:
                    self._text(value, out)
                continue
            if char == '\n':
                self._newline(out)
            elif char != '\r':
                self._text(char, out)
        return out

    def expire(self, now=None):
        out = []
        now = time.monotonic() if now is None else now
        if self.pending_at is not None and now - self.pending_at >= GRACE_SECONDS:
            self._release(out)
        if self.line and self.line_at is not None and now - self.line_at >= GRACE_SECONDS:
            self.pending = self.line
            self.line = ""
            self.line_at = None
            self._release(out)
            self._drop("plain_line_fallback", out)
        return out

    def finish(self):
        if self.closed:
            return []
        out = []
        if self.mode == 'metadata':
            # A complete JSON object at EOF is valid; partial serialization is hidden.
            self._metadata(out)
        elif self.mode == 'probe':
            self._text(self.probe, out)
        if self.line.strip():
            self.pending = self.line.strip()
        self._release(out)
        self.closed = True
        return out

    def cancel(self):
        if self.mode == 'metadata':
            self.dropped['cancelled_metadata'] += 1
        self.pending = None
        self.pending_at = None
        self.probe = self.line = ''
        self.closed = True

    def diagnostics(self):
        return {'enabled': True, 'segments': [asdict(s) for s in self.segments],
                'dropped': dict(self.dropped), 'trailers': min(self.trailers, MAX_TRAILERS),
                'first_segment_wait_ms': self.waits[0] if self.waits else None}


def parse_dialogue(text: str) -> Dialogue:
    parser = DialogueStreamParser()
    events = parser.feed(text) + parser.finish()
    return Dialogue(tuple(parser.segments), ''.join(e.text for e in events if isinstance(e, Delta)))
