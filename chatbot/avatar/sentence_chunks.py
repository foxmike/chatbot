"""Natural-mode text chunking: one complete sentence per TTS request.

Kept apart from `speech_chunks.py`, which holds the proven adaptive ("fast") chunker, planner
and text filter unchanged. This chunker reuses their bookkeeping and cleanup.
"""

import re

from chatbot.avatar.protocol import validate_speech_mode
from chatbot.avatar.speech_chunks import AdaptiveChunker, SpeechChunk, spoken_text

# Sentence terminators: runs of . ! ?, an ellipsis character, then any closing quotes,
# brackets or markdown emphasis. A boundary needs whitespace after it, so "3.14" and
# "example.com" never end a sentence; text ending exactly at the punctuation waits for more.
_SENTENCE_END = re.compile(r"(?:[.!?]+|…+)[\"'”’)\]*_~`]*(?=\s)")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "mt.", "vs.", "e.g.", "i.e.",
    "a.m.", "p.m.", "inc.", "ltd.", "co.", "no.", "fig.", "approx.", "u.s.", "u.k.",
}
_INITIAL = re.compile(r"[A-Za-z]\.")
_LIST_MARKER = re.compile(r"\d{1,3}\.")
_OPENERS = "\"'“‘([*_~`"


class SentenceChunker(AdaptiveChunker):
    """Emit one complete sentence at a time.

    Text accumulates as it streams; nothing is cut until a sentence is finished, however long
    it is. There is no size limit, no clause or word cut, no first-chunk timeout and no
    playback-driven cut: `max_chars` and `urgent` are accepted for interface compatibility with
    AdaptiveChunker and ignored. A paragraph break also ends a unit, and the remaining text is
    flushed when the response finishes. Display of the text is unaffected: only the speech
    branch waits.
    """

    def take(self, now, max_chars=None, urgent=False):
        content = self.pending.lstrip()
        if not content:
            return None
        offset = len(self.pending) - len(content)
        end, reason = self._unit_end(content)
        if end is None:
            if not self.finished:
                return None
            end, reason = len(content), "final"
        if self.count == 0 and reason == "sentence":
            reason = "first sentence"
        raw = self.pending[:offset + end]
        self.pending = self.pending[offset + end:]
        self.count += 1
        text = spoken_text(raw)
        started, ready = self._arrival_span(raw, now)
        return SpeechChunk(text, reason, now, started, ready) if text else self.take(now)

    @staticmethod
    def _unit_end(content):
        """(end offset, reason) of the first complete sentence or paragraph, else (None, None)."""
        paragraph = _PARAGRAPH_BREAK.search(content)
        limit = paragraph.start() if paragraph and paragraph.start() > 0 else None
        for match in _SENTENCE_END.finditer(content):
            if limit is not None and match.end() > limit:
                break
            verdict = SentenceChunker._ends_sentence(content, match)
            if verdict is None:      # cannot tell yet: the next character has not arrived
                break
            if verdict:
                return match.end(), "sentence"
        if limit is not None and content[:limit].strip():
            return limit, "paragraph"
        return None, None

    @staticmethod
    def _ends_sentence(content, match):
        """True: the sentence ends here; False: it continues; None: wait for more text."""
        end = match.end()
        words = content[:end].split()
        bare = words[-1].lstrip(_OPENERS) if words else ""
        if bare.lower() in _ABBREVIATIONS:
            return False
        if _INITIAL.fullmatch(bare) and bare != "I.":   # "J. K. Rowling", but "...so do I."
            return False
        if _LIST_MARKER.fullmatch(bare):
            before = content[:end - len(bare)]
            if not before.strip() or before.endswith("\n"):   # "1." opening a line is a list marker
                return False
        # ! and ? are decisive. A full stop or ellipsis followed by a lowercase letter continues
        # the sentence ("approx. five", "wait... what"), so it needs the next character.
        if "!" in match.group() or "?" in match.group():
            return True
        following = content[end:].lstrip()
        if not following:
            return None
        return not following[0].islower()


def make_chunker(mode):
    """The text chunker for a (validated) TTS mode."""
    return SentenceChunker() if validate_speech_mode(mode) == "natural" else AdaptiveChunker()
