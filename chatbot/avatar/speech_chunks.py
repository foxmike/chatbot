"""Small, deterministic text filter, pull-based speech chunker, synthesis planner and edge trimming."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import re

import numpy as np


_HIDDEN_OPEN = re.compile(r"<(think|analysis|reasoning)(?:\s[^>]*)?>", re.I)
_HIDDEN_CLOSE = re.compile(r"</(?:think|analysis|reasoning)\s*>", re.I)
_SENTENCE_END = re.compile(r"[.!?][\"'’)]*(?=\s|$)")
_CLAUSE_END = re.compile(r"[,;:](?=\s|$)")
_DANGLING = {"a", "an", "the", "to", "of", "and", "or", "but", "in", "on", "at", "for", "with", "that",
             "so", "as", "if", "my", "your", "our", "his", "her", "its", "their", "is", "are", "was", "be",
             "about", "by", "from", "into", "than", "because", "while", "when", "what", "who"}
_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "etc.", "e.g.", "i.e."}


class VisibleTextFilter:
    """Discard hidden reasoning tags even when their delimiters span tokens."""

    def __init__(self):
        self.pending = ""
        self.hidden = False

    def feed(self, text):
        self.pending += text
        visible = []
        while self.pending:
            if self.hidden:
                match = _HIDDEN_CLOSE.search(self.pending)
                if match:
                    self.pending = self.pending[match.end():]
                    self.hidden = False
                    continue
                self.pending = self.pending[-24:]
                break
            match = _HIDDEN_OPEN.search(self.pending)
            if match:
                visible.append(self.pending[:match.start()])
                self.pending = self.pending[match.end():]
                self.hidden = True
                continue
            unfinished = self.pending.rfind("<")
            if unfinished >= 0 and ">" not in self.pending[unfinished:]:
                visible.append(self.pending[:unfinished])
                self.pending = self.pending[unfinished:]
                break
            visible.append(self.pending)
            self.pending = ""
        return "".join(visible)

    def finish(self):
        if self.hidden:
            self.pending = ""
            return ""
        remaining, self.pending = self.pending, ""
        return remaining if "<" not in remaining else ""


def spoken_text(text):
    """Remove formatting that is useful on screen but should never be read."""
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"[`*_#~]", "", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class SpeechChunk:
    text: str
    reason: str
    at: float
    started_at: float = 0.0  # first character of this chunk arrived from the LLM
    ready_at: float = 0.0    # last character of this chunk arrived from the LLM
    pause_seconds: float | None = None


class AdaptiveChunker:
    """Accumulates LLM text; the TTS stage pulls a chunk when it is free.

    Cutting at pull time (not arrival time) lets the size follow the playback
    buffer that actually exists when synthesis starts. Boundary preference:
    sentence end, then clause end, then a word boundary.
    """

    def __init__(self):
        self.pending = ""
        self.first_token_at = None
        self.finished = False
        self.count = 0
        self.fed = 0           # characters ever fed
        self.consumed = 0      # characters already emitted or discarded
        self.arrival_ends = []  # cumulative character count after each feed
        self.arrival_times = []

    def feed(self, text, now):
        if text and self.first_token_at is None:
            self.first_token_at = now
        if text:
            self.fed += len(text)
            self.arrival_ends.append(self.fed)
            self.arrival_times.append(now)
        self.pending += text

    def finish(self):
        self.finished = True

    @property
    def done(self):
        return self.finished and not self.pending.strip()

    def age(self, now):
        return 0.0 if self.first_token_at is None else now - self.first_token_at

    def take(self, now, max_chars, urgent=False):
        """Return the next chunk of at most ~`max_chars`, or None to keep waiting.

        `urgent` means playback is about to run dry (or, for the first chunk,
        latency matters more than a perfect boundary): accept a word cut.
        """
        content = self.pending.lstrip()
        if not content:
            return None
        offset = len(self.pending) - len(content)
        first = self.count == 0
        sentences, clauses = self._boundaries(content)
        limit = int(max_chars * 1.2)
        end = reason = None
        if self.finished and len(content) <= limit:
            end, reason = len(content), "final"
        elif first:
            if (s := next((x for x in sentences if 10 <= x <= limit), None)):
                end, reason = s, "first sentence"
            elif (c := next((x for x in clauses if 14 <= x <= limit), None)):
                end, reason = c, "first phrase"
        else:
            if (s := [x for x in sentences if max(12, 0.3 * max_chars) <= x <= limit]):
                end, reason = s[-1], "sentence"
            elif (c := [x for x in clauses if max(20, 0.5 * max_chars) <= x <= max_chars]):
                end, reason = c[-1], "phrase"
        if end is None and len(content) > limit:
            end = self._word_boundary(content, max_chars) or self._word_boundary(content, len(content))
            reason = "first timeout" if first else "length"
        if end is None and urgent and len(content) >= (20 if first else 12):
            end = self._word_boundary(content, min(len(content), max_chars))
            reason = "first timeout" if first else "timeout"
        if not end:
            return None
        raw = self.pending[:offset + end]
        self.pending = self.pending[offset + end:]
        self.count += 1
        text = spoken_text(raw)
        started, ready = self._arrival_span(raw, now)
        return SpeechChunk(text, reason, now, started, ready) if text else self.take(now, max_chars, urgent)

    @staticmethod
    def _boundaries(content):
        sentences = []
        for match in _SENTENCE_END.finditer(content):
            words = content[:match.end()].split()
            if not words or words[-1].lower() not in _ABBREVIATIONS:
                sentences.append(match.end())
        return sentences, [m.end() for m in _CLAUSE_END.finditer(content)]

    def _arrival_span(self, raw, now):
        """Arrival times of the first and last non-space characters of `raw`."""
        lead = len(raw) - len(raw.lstrip())
        first_abs = self.consumed + lead + 1
        last_abs = self.consumed + len(raw.rstrip())
        self.consumed += len(raw)
        if not self.arrival_ends or last_abs < first_abs:
            return now, now
        first = self.arrival_times[min(bisect_left(self.arrival_ends, first_abs), len(self.arrival_times) - 1)]
        last = self.arrival_times[min(bisect_left(self.arrival_ends, last_abs), len(self.arrival_times) - 1)]
        drop = bisect_right(self.arrival_ends, self.consumed) - 1
        if drop > 0:
            del self.arrival_ends[:drop], self.arrival_times[:drop]
        return first, last

    @staticmethod
    def _word_boundary(text, limit):
        if limit >= len(text) and text[-1].isspace():
            return len(text)
        end = text.rfind(" ", 0, limit + 1)
        # Do not strand a function word ("...spend time with | you") at a chunk end.
        while end > 0 and text[:end].split()[-1].lower().strip(",;:") in _DANGLING:
            end = text.rfind(" ", 0, end)
        return end + 1 if end > 0 else None


class SpeechPlanner:
    """Sizes each chunk so its synthesis finishes before queued audio runs out.

    Qwen's cost is measured to be linear in audio length (no fixed overhead),
    so a chunk of D seconds needs about `rate * D` seconds; playback keeps going
    only if that fits inside the audio already queued ("lead"). Rate and
    audio-per-character are learned from completed chunks (decayed sums).
    """

    FIRST_CHARS = 38      # first chunk: small enough for a quick start
    MAX_CHARS = 150
    MIN_CHARS = 28
    MAX_AUDIO = 8.0       # also bounds how long an uninterruptible synthesis can run
    SLACK = 0.25          # resample, AVTR hand-off and browser scheduling
    SAFETY = 1.1
    TARGET_LEAD = 3.0     # queued audio below this earns longer sentence-end pauses
    MAX_EXTRA_PAUSE = 0.26

    def __init__(self, rate=0.9, audio_per_char=0.065, startup=0.2):
        self.synth = rate * 10.0       # decayed sums seeded with 10s of pseudo-audio
        self.audio = 10.0
        self.chars = 10.0 / audio_per_char
        self.startup = startup         # PCM ready -> first sound for an idle player
        self.horizon = 0.0             # clock time when all queued audio has played
        self.estimates = {}

    @property
    def rate(self):
        return self.synth / self.audio

    @property
    def audio_per_char(self):
        return self.audio / self.chars

    def lead(self, now):
        return max(0.0, self.horizon - now)

    def extra_pause(self, now):
        """Silence to add after a finished sentence while the audio queue is short.

        Speech has natural pauses at sentence ends; stretching them (up to ~0.4 s in
        total) lets a synthesizer that runs near real time rebuild its lead where a
        listener does not notice, instead of stalling mid-phrase later.
        """
        shortfall = max(0.0, self.TARGET_LEAD - self.lead(now)) / self.TARGET_LEAD
        return self.MAX_EXTRA_PAUSE * min(1.0, shortfall)

    def budget(self, now, count):
        if count == 0:
            return self.FIRST_CHARS
        audio = (self.lead(now) - self.SLACK) / (self.rate * self.SAFETY)
        audio = min(self.MAX_AUDIO, max(1.0, audio))
        return int(min(self.MAX_CHARS, max(self.MIN_CHARS, audio / (self.audio_per_char * self.SAFETY))))

    def audio_ready(self, key, now, duration):
        """Register a finished waveform; returns when it is expected to finish playing."""
        start = max(self.horizon, now + self.startup)
        self.horizon = start + duration
        self.estimates[key] = self.horizon
        return self.horizon

    def observe(self, chars, audio_seconds, synth_seconds):
        if chars and audio_seconds > 0:
            self.synth = 0.7 * self.synth + synth_seconds
            self.audio = 0.7 * self.audio + audio_seconds
            self.chars = 0.7 * self.chars + chars

    def correct(self, key, actual_end):
        """Shift the projection by what the browser really scheduled for `key`."""
        estimate = self.estimates.pop(key, None)
        if estimate is None:
            return
        delta = actual_end - estimate
        self.horizon += delta
        for other in self.estimates:
            self.estimates[other] += delta


def edge_silence(audio, sr, threshold=0.015):
    """(leading, trailing) silence of a waveform in seconds."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if not len(loud):
        return len(audio) / sr, 0.0
    return int(loud[0]) / sr, (len(audio) - 1 - int(loud[-1])) / sr


def trim_silence(audio, sr, sentence_end, threshold=0.015):
    """Cap the silence Qwen leaves at a waveform's edges.

    A pause after a finished sentence stays natural (140 ms); a chunk that
    continues the sentence keeps only 30 ms so the join is not audible.
    """
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if not len(loud):
        return audio
    start = max(0, int(loud[0]) - int(0.012 * sr))
    end = min(len(audio), int(loud[-1]) + 1 + int((0.14 if sentence_end else 0.03) * sr))
    trimmed = np.array(audio[start:end], dtype=audio.dtype)
    fade = min(int(0.006 * sr), len(trimmed) // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=trimmed.dtype)
        if start > 0:
            trimmed[:fade] *= ramp
        if end < len(audio):
            trimmed[-fade:] *= ramp[::-1]
    return trimmed
