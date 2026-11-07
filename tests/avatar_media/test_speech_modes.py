"""Natural (sentence) and Fast (adaptive) speech chunking, from chunker to Qwen requests."""
import asyncio

import numpy as np
import pytest

from chatbot.avatar import media
from chatbot.avatar.coordinator import AvatarBinding, AvatarCoordinator
from chatbot.avatar.media import MediaError
from chatbot.avatar.protocol import (
    DEFAULT_SPEECH_MODE,
    SPEECH_MODES,
    Diagnostic,
    validate_speech_mode,
)
from chatbot.avatar.sentence_chunks import SentenceChunker, make_chunker
from chatbot.avatar.speech_chunks import AdaptiveChunker, SpeechPlanner

from .helpers import Harness, wait_for
from .test_runtime_adapter import rig


def sentences(pieces, finish=True):
    """Feed streamed tokens and pull every chunk the way the TTS stage does."""
    chunker = SentenceChunker()
    out = []
    for index, piece in enumerate(pieces):
        chunker.feed(piece, index * 0.01)
        while chunk := chunker.take(index * 0.01):
            out.append(chunk.text)
    if finish:
        chunker.finish()
        while chunk := chunker.take(1.0):
            out.append(chunk.text)
    return out


def spoken(h):
    """The texts sent to Qwen3-TTS, in request order."""
    return [m["text"] for m in h.tts.sent if m["type"] == "speak"]


async def talk(h, pieces, **kwargs):
    turn = await h.engine.begin_response(**kwargs)
    for piece in pieces:
        await h.engine.feed_text(turn, piece)
    await h.engine.end_response(turn)
    ended = await asyncio.wait_for(h.engine.wait_turn(turn), 10)
    return turn, ended


# --------------------------------------------------------------------------- defaults


def test_natural_is_the_default_and_modes_are_validated():
    assert DEFAULT_SPEECH_MODE == "natural"
    assert SPEECH_MODES == ("natural", "fast")
    assert isinstance(make_chunker(DEFAULT_SPEECH_MODE), SentenceChunker)
    assert type(make_chunker("fast")) is AdaptiveChunker
    for bad in ("", "Natural", "slow", None, 3, ["fast"]):
        with pytest.raises(ValueError, match="Invalid TTS mode"):
            validate_speech_mode(bad)
        if isinstance(bad, str):
            with pytest.raises(ValueError):
                make_chunker(bad)


async def test_engine_defaults_to_natural_and_rejects_unknown_modes():
    h = await Harness().ready()
    turn = await h.engine.begin_response()
    assert h.engine.metrics["speech_mode"] == "natural"
    await h.engine.cancel(turn)
    with pytest.raises(MediaError, match="Invalid TTS mode"):
        await h.engine.begin_response(speech_mode="slow")


# --------------------------------------------------------------------------- Natural chunker


def test_each_complete_sentence_is_one_unit_and_text_order_is_preserved():
    assert sentences(["Hello there. How are you? I am fine!"]) == [
        "Hello there.", "How are you?", "I am fine!"]


def test_commas_semicolons_and_colons_never_split():
    text = "First, we wait; then, as planned: we go, and we stay, because it is late."
    assert sentences([text]) == [text]


def test_long_sentences_stay_intact_beyond_the_150_character_limit():
    long_sentence = ("This sentence just keeps going " * 14).strip() + " until it finally ends."
    assert len(long_sentence) > 400
    assert sentences([long_sentence + " Short."]) == [long_sentence, "Short."]
    # Even streamed one character at a time.
    assert sentences(list(long_sentence + " Short.")) == [long_sentence, "Short."]


def test_streaming_token_boundaries_never_split_a_sentence():
    pieces = ["Hel", "lo wor", "ld", ".", " Next ", "sentence", "!", " Last", " one."]
    assert sentences(pieces) == ["Hello world.", "Next sentence!", "Last one."]
    # Nothing is released while the punctuation might still be part of something longer.
    chunker = SentenceChunker()
    for piece in ["The value is 3", ".", "14 dollars."]:
        chunker.feed(piece, 0.0)
        assert chunker.take(0.0) is None


def test_multiple_sentences_in_one_token_become_separate_requests():
    assert sentences(["One. Two? Three! Four."]) == ["One.", "Two?", "Three!", "Four."]


def test_abbreviations_and_initials_do_not_end_sentences():
    assert sentences(["Dr. Smith met Mrs. Jones at 5 p.m. today. J. K. Rowling came too."]) == [
        "Dr. Smith met Mrs. Jones at 5 p.m. today.", "J. K. Rowling came too."]
    assert sentences(["Use e.g. a fork, i.e. not a spoon. Done."]) == [
        "Use e.g. a fork, i.e. not a spoon.", "Done."]
    # The pronoun "I." at a sentence end is still an end.
    assert sentences(["So do I. Yes."]) == ["So do I.", "Yes."]


def test_decimal_numbers_urls_and_versions_do_not_end_sentences():
    assert sentences(["It costs 3.50 dollars. Version 2.0.1 is out. See example.com now."]) == [
        "It costs 3.50 dollars.", "Version 2.0.1 is out.", "See example.com now."]


def test_closing_quotes_brackets_and_emphasis_stay_with_their_sentence():
    assert sentences(['He said "Stop." Then he left. (It was late.) It was **done.** Next!']) == [
        'He said "Stop."', "Then he left.", "(It was late.)", "It was done.", "Next!"]
    assert sentences(["She asked, “Really?” He nodded."]) == ["She asked, “Really?”", "He nodded."]


def test_ellipses_and_lowercase_continuations_do_not_split():
    assert sentences(["Wait... what? Really."]) == ["Wait... what?", "Really."]
    assert sentences(["It was approx. five miles. Then more."]) == [
        "It was approx. five miles.", "Then more."]


def test_numbered_list_markers_are_not_sentence_ends():
    assert sentences(["1. First item. 2. Second item."]) == ["1. First item.", "2. Second item."]


def test_final_incomplete_sentence_is_emitted_when_the_response_finishes():
    chunker = SentenceChunker()
    chunker.feed("This one is done. And this one never ends", 0.0)
    assert chunker.take(0.0).text == "This one is done."
    assert chunker.take(5.0, urgent=True) is None          # waiting never times out
    chunker.finish()
    final = chunker.take(6.0)
    assert (final.text, final.reason) == ("And this one never ends", "final")
    assert chunker.done


def test_paragraph_breaks_end_a_unit_without_punctuation():
    assert sentences(["A heading without a period\n\nThen a sentence."]) == [
        "A heading without a period", "Then a sentence."]


def test_ignores_size_and_urgency_arguments():
    chunker = SentenceChunker()
    chunker.feed("A sentence that is waiting for its ending punctuation to arrive", 0.0)
    for max_chars, urgent in ((1, True), (10, True), (150, True), (None, False)):
        assert chunker.take(99.0, max_chars, urgent) is None
    chunker.feed(". Next", 0.1)
    assert chunker.take(99.0, 1, True).text.endswith("arrive.")


def test_spoken_text_cleanup_still_applies():
    assert sentences(["Read **this** [link](http://x.io) now. Then `code` ends."]) == [
        "Read this link now.", "Then code ends."]


# --------------------------------------------------------------------------- Fast is unchanged


def test_fast_mode_parameters_are_unchanged():
    assert (SpeechPlanner.FIRST_CHARS, SpeechPlanner.MIN_CHARS, SpeechPlanner.MAX_CHARS) == (38, 28, 150)
    assert (media.FIRST_CHUNK_WAIT, media.URGENT_LEAD) == (0.3, 0.8)
    assert (media.MAX_LEAD_SECONDS, media.MAX_AUDIO_CHUNKS) == (10.0, 3)


def test_fast_mode_still_cuts_long_text_at_clause_and_word_boundaries():
    text = ("This long sentence keeps going, and going, with clauses that run on for quite a while, "
            "so the adaptive chunker must cut it somewhere before the end of the sentence arrives.")
    chunker = AdaptiveChunker()
    chunker.feed(text, 0.0)
    chunker.finish()
    planner = SpeechPlanner()
    chunks = []
    while chunk := chunker.take(1.0, planner.budget(1.0, chunker.count), True):
        chunks.append(chunk)
    assert len(chunks) > 1
    assert chunks[0].reason in ("first phrase", "first timeout", "first sentence")
    assert " ".join(c.text for c in chunks).split() == text.split()
    # Natural keeps the very same text as a single request.
    assert sentences([text]) == [text]


# --------------------------------------------------------------------------- engine: Natural


async def test_natural_sends_each_complete_sentence_to_qwen_as_one_request():
    h = await Harness().ready()
    pieces = ["Hello th", "ere, my frie", "nd. How are", " you today? ", "I hope", " well."]
    turn, ended = await talk(h, pieces)
    assert ended.status == "completed"
    assert spoken(h) == ["Hello there, my friend.", "How are you today?", "I hope well."]
    # Speech reaches the browser in the same order, one segment per sentence.
    ids = [m["id"] for m in h.tts.sent if m["type"] == "speak"]
    assert ids == [f"{h.engine.metrics['turn']}:{n}" for n in (1, 2, 3)]


async def test_natural_never_splits_a_long_sentence_and_reports_tts_failure():
    h = await Harness().ready()
    long_sentence = ("This is a deliberately long sentence that goes on and on, with clauses, "
                     "commas, and more words than the old limit allowed, ") * 4 + "and then it ends."
    assert len(long_sentence) > 300
    turn, ended = await talk(h, [long_sentence])
    assert spoken(h) == [" ".join(long_sentence.split())]
    assert ended.status == "completed"


async def test_natural_reports_a_synthesis_failure_instead_of_splitting_or_switching_mode():
    h = await Harness().ready()
    original = h.tts.react

    async def fail(message):
        if message["type"] == "speak":
            await h.tts.messages.put({"type": "error", "id": message["id"], "error": "too long"})
            return
        await original(message)

    h.tts.react = fail
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "A sentence that the synthesizer cannot handle. Another one.")
    await h.engine.end_response(turn)
    ended = await asyncio.wait_for(h.engine.wait_turn(turn), 10)
    assert ended.status == "failed" and ended.error
    # Exactly the one complete sentence was requested; no smaller pieces, no Fast retry.
    assert spoken(h) == ["A sentence that the synthesizer cannot handle."]
    assert h.engine.metrics["speech_mode"] == "natural"


async def test_natural_flushes_the_final_unfinished_sentence():
    h = await Harness().ready()
    turn, ended = await talk(h, ["First one. And the last words trail off"])
    assert spoken(h) == ["First one.", "And the last words trail off"]
    assert ended.status == "completed"


async def test_hidden_reasoning_never_reaches_tts_in_natural_mode():
    h = await Harness().ready()
    pieces = ["<thi", "nk>Secret plan. Do not say this.</th", "ink>Hello there. ", "<analysis>x. y.</analysis>Bye."]
    turn, ended = await talk(h, pieces)
    assert spoken(h) == ["Hello there.", "Bye."]
    assert "ecret" not in " ".join(spoken(h))


async def test_cancelling_while_waiting_for_punctuation_sends_nothing_to_tts():
    h = await Harness(auto_playback=False).ready()
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "I am still talking and have not reached the end of this thought")
    await asyncio.sleep(0.4)      # far longer than the Fast first-chunk timeout (0.3 s)
    assert spoken(h) == []
    await h.engine.cancel(turn)
    ended = await asyncio.wait_for(h.engine.wait_turn(turn), 2)
    assert ended.status == "cancelled"
    assert spoken(h) == [] and h.engine.turn is None
    # Text arriving after the cancel is ignored.
    assert await h.engine.feed_text(turn, "More. Words.") is False
    await asyncio.sleep(0.05)
    assert spoken(h) == []


async def test_natural_does_not_throttle_the_llm_while_waiting_for_a_sentence_end():
    h = await Harness(auto_playback=False).ready()
    turn = await h.engine.begin_response()
    task = asyncio.create_task(h.engine.feed_text(turn, "word " * 400))   # 2000 chars, no end
    await asyncio.sleep(0.2)
    assert task.done()            # Fast would hold this call back; Natural must not
    await h.engine.cancel(turn)


async def test_natural_does_not_cut_for_low_playback_audio_or_the_first_chunk_timeout():
    h = await Harness(auto_playback=False).ready()
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "Well, I think that, perhaps, it might possibly be the case that")
    await asyncio.sleep(0.5)      # playback is idle, queued audio is zero, the 300 ms timeout passed
    assert spoken(h) == []
    await h.engine.feed_text(turn, " this finally ends. Then more")
    await wait_for(lambda: spoken(h))
    assert spoken(h) == ["Well, I think that, perhaps, it might possibly be the case that this finally ends."]
    await h.engine.cancel(turn)


async def test_later_sentences_synthesize_while_earlier_ones_are_still_playing():
    h = await Harness(auto_playback=False).ready()     # the browser has not finished anything yet
    turn = await h.engine.begin_response()
    await h.engine.feed_text(turn, "One is here. Two is here. Three is here. Four")
    await wait_for(lambda: len(spoken(h)) == 3)         # synthesis ran ahead of playback
    assert spoken(h) == ["One is here.", "Two is here.", "Three is here."]
    await h.engine.cancel(turn)


# --------------------------------------------------------------------------- engine: Fast


async def test_fast_mode_keeps_cutting_long_text_into_adaptive_chunks():
    h = await Harness().ready()
    long_sentence = ("This is a deliberately long sentence that goes on and on, with clauses, "
                     "commas, and more words than the limit allows, ") * 4 + "and then it ends."
    turn, ended = await talk(h, [long_sentence], speech_mode="fast")
    texts = spoken(h)
    assert len(texts) > 1 and all(len(t) <= int(150 * 1.2) + 5 for t in texts)
    assert " ".join(texts).split() == long_sentence.split()
    assert h.engine.metrics["mid_sentence_cuts"] >= 1
    assert h.engine.metrics["speech_mode"] == "fast"


async def test_fast_mode_still_throttles_pending_text():
    h = await Harness(auto_playback=False).ready()
    h.tts.hold = asyncio.Event()
    turn = await h.engine.begin_response(speech_mode="fast")
    task = asyncio.create_task(h.engine.feed_text(turn, "word " * 400))
    await asyncio.sleep(0.2)
    assert not task.done()
    h.tts.hold.set()
    await h.engine.cancel(turn)


# --------------------------------------------------------------------------- telemetry


async def test_turn_diagnostics_report_the_mode_and_chunk_statistics():
    h = await Harness().ready()
    turn, ended = await talk(h, ["Hello there, my friend. ", "How are you today? Fine."])
    summary = next(e for e in h.of(Diagnostic) if e.name == "turn_summary")
    assert summary.data["speech_mode"] == "natural"
    metrics = summary.data["metrics"]
    assert metrics["chunks"] == 3
    assert metrics["sentence_cuts"] == 3 and metrics["mid_sentence_cuts"] == 0
    assert metrics["chunk_chars"] == len("Hello there, my friend.") + len("How are you today?") + len("Fine.")
    assert "playback_gaps" in metrics
    text = summary.data["summary"]
    assert "TTS mode natural" in text and "chars/chunk" in text and "sentence-boundary" in text
    assert "first LLM token" in text and "first complete sentence" in text
    assert "first speech chunk" in text and "first PCM" in text and "RTF" in text
    planned = [e for e in h.of(Diagnostic) if e.name == "chunk_planned"]
    assert planned and all(e.data["speech_mode"] == "natural" for e in planned)


async def test_fast_turns_are_labelled_fast():
    h = await Harness().ready()
    turn, ended = await talk(h, ["Hello there, my friend."], speech_mode="fast")
    summary = next(e for e in h.of(Diagnostic) if e.name == "turn_summary")
    assert summary.data["speech_mode"] == "fast"
    assert "TTS mode fast" in summary.data["summary"]


# --------------------------------------------------------------------------- per-turn mode capture


async def test_a_turn_keeps_its_starting_mode_and_the_next_turn_uses_the_new_one(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    assert binding.speech_mode == "natural"
    first = await coordinator.begin(binding, source="typed")
    assert h.engine.metrics["speech_mode"] == "natural"
    first_turn = h.engine.turn
    binding.speech_mode = "fast"                       # the dropdown changes mid-turn
    assert first_turn.speech_mode == "natural"          # ...the running turn is unaffected
    assert isinstance(first_turn.chunker, SentenceChunker)
    await coordinator.interrupt(binding)
    second = await coordinator.begin(binding, source="spoken")
    assert h.engine.metrics["speech_mode"] == "fast"
    assert type(h.engine.turn.chunker) is AdaptiveChunker
    await coordinator.close()


async def test_typed_and_spoken_turns_both_honor_the_binding_mode(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    for mode in ("fast", "natural"):
        for source in ("typed", "spoken"):
            binding.speech_mode = mode
            turn = await coordinator.begin(binding, source=source)
            assert h.engine.metrics["speech_mode"] == mode, (mode, source)
            await coordinator.interrupt(binding)
    await coordinator.close()


async def test_bindings_for_different_sessions_keep_independent_modes(tmp_path):
    h, host, ws, binding, coordinator, _ = await rig(tmp_path)
    other = AvatarBinding("session-2", "user-1", "companion-1", ws)
    other.speech_mode = "fast"
    assert binding.speech_mode == "natural" and other.speech_mode == "fast"
    binding.speech_mode = "fast"
    other.speech_mode = "natural"
    assert (binding.speech_mode, other.speech_mode) == ("fast", "natural")
    await coordinator.close()
