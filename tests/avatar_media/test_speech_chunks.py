"""Chunker, planner and silence-trimming behaviour, ported unchanged from my_avatar."""
import unittest

import numpy as np

from chatbot.avatar.speech_chunks import (
    AdaptiveChunker,
    SpeechPlanner,
    VisibleTextFilter,
    spoken_text,
    trim_silence,
)


def drain(chunker, planner, now=0.0, urgent=True):
    """Pull every chunk the way the pipeline does, advancing a fake clock."""
    chunks = []
    while (chunk := chunker.take(now, planner.budget(now, chunker.count), urgent)):
        chunks.append(chunk)
        duration = len(chunk.text) * 0.058
        now += duration * 0.8
        planner.observe(len(chunk.text), duration, duration * 0.8)
        planner.audio_ready(len(chunks), now, duration)
    return chunks


class ChunkingTests(unittest.TestCase):
    def test_hidden_reasoning_never_reaches_speech(self):
        filtered = VisibleTextFilter()
        pieces = ["<thi", "nk>Do not say this.</th", "ink>Hello there."]
        visible = "".join(filtered.feed(part) for part in pieces) + filtered.finish()
        self.assertEqual(visible, "Hello there.")

    def test_first_chunk_takes_an_early_sentence_without_waiting(self):
        chunker = AdaptiveChunker()
        chunker.feed("That sounds lovely. And then I wondered what", 0.0)
        chunk = chunker.take(0.1, SpeechPlanner.FIRST_CHARS)
        self.assertEqual((chunk.text, chunk.reason), ("That sounds lovely.", "first sentence"))

    def test_first_chunk_can_use_a_clause_before_the_sentence_ends(self):
        chunker = AdaptiveChunker()
        chunker.feed("I have been waiting to tell you something, because ", 0.0)
        chunk = chunker.take(0.1, SpeechPlanner.FIRST_CHARS)
        self.assertEqual((chunk.text, chunk.reason),
                         ("I have been waiting to tell you something,", "first phrase"))

    def test_waits_for_more_text_unless_urgent_and_never_splits_a_word(self):
        chunker = AdaptiveChunker()
        chunker.feed("I really want to explain what happened before we met", 0.0)
        self.assertIsNone(chunker.take(0.1, 60))
        chunk = chunker.take(0.5, 60, urgent=True)
        self.assertEqual(chunk.reason, "first timeout")
        # The trailing, possibly unfinished word stays pending rather than being cut.
        self.assertEqual(chunk.text, "I really want to explain what happened before we")
        self.assertEqual(chunker.pending.strip(), "met")

    def test_chunk_size_follows_budget_and_prefers_sentence_boundaries(self):
        text = ("It sounds lovely. I can see why you remember it so clearly. "
                "Tell me what happened next after that, because I am curious.")
        chunker = AdaptiveChunker()
        chunker.feed(text, 0.0)
        chunker.finish()
        chunker.take(0.0, 20)  # first chunk
        small = chunker.take(0.0, 60)
        self.assertEqual((small.text, small.reason), ("I can see why you remember it so clearly.", "sentence"))
        big = AdaptiveChunker()
        big.feed(text, 0.0)
        big.finish()
        big.take(0.0, 20)
        self.assertEqual(big.take(0.0, 150).reason, "final")

    def test_word_cut_does_not_end_on_a_function_word(self):
        chunker = AdaptiveChunker()
        chunker.feed("We spent a long evening talking about the history of the", 0.0)
        chunker.take(0.0, 30, urgent=True)
        chunk = chunker.take(0.0, 20, urgent=True)
        self.assertNotIn(chunk.text.split()[-1].lower(), ("the", "of", "a", "about"))

    def test_following_short_sentence_joins_next_thought(self):
        chunker = AdaptiveChunker()
        chunker.feed("It sounds lovely.", 0.0)
        chunker.take(0.1, 44)
        text = ("How old are your kids now? I'd love to hear more about what "
                "parenting looks like for you day to day.")
        chunker.feed(" " + text, 0.2)
        chunker.finish()
        planner = SpeechPlanner()
        planner.audio_ready("queued", 0.3, 8.0)  # a healthy buffer allows a longer thought
        self.assertEqual([c.text for c in drain(chunker, planner, 0.3)], [text])

    def test_order_and_final_text_survive_incremental_tokens(self):
        response = ("That sounds lovely. Tell me what happened next, because the middle part is where "
                    "stories usually get interesting and I would hate to miss it. I am listening")
        chunker = AdaptiveChunker()
        words = response.split(" ")
        chunks = []
        planner = SpeechPlanner()
        for index, word in enumerate(words):
            chunker.feed(word + (" " if index < len(words) - 1 else ""), index * 0.05)
            while chunk := chunker.take(index * 0.05, planner.budget(index * 0.05, chunker.count)):
                chunks.append(chunk)
        chunker.finish()
        chunks.extend(drain(chunker, planner, 5.0))
        self.assertEqual(" ".join(chunk.text for chunk in chunks), response)
        self.assertEqual(chunks[-1].reason, "final")
        self.assertTrue(chunker.done)

    def test_provenance_separates_llm_arrival_from_release(self):
        chunker = AdaptiveChunker()
        chunker.feed("Hello there, ", 1.0)
        chunker.feed("my friend. More", 1.4)
        chunk = chunker.take(2.5, 60)
        self.assertEqual(chunk.text, "Hello there, my friend.")
        self.assertEqual((chunk.started_at, chunk.ready_at, chunk.at), (1.0, 1.4, 2.5))

    def test_spoken_text_removes_markup_but_keeps_words(self):
        self.assertEqual(spoken_text("**Hello** [friend](https://example.com)."), "Hello friend.")


class PlannerTests(unittest.TestCase):
    def test_first_chunk_is_small_and_later_chunks_grow_with_queued_audio(self):
        planner = SpeechPlanner()
        self.assertEqual(planner.budget(0.0, 0), SpeechPlanner.FIRST_CHARS)
        planner.audio_ready("a", 0.0, 2.5)
        early = planner.budget(0.5, 1)
        planner.audio_ready("b", 0.5, 6.0)
        self.assertGreater(planner.budget(0.5, 2), early)
        self.assertLessEqual(planner.budget(0.5, 2), SpeechPlanner.MAX_CHARS)

    def test_slow_synthesis_shrinks_the_budget(self):
        fast, slow = SpeechPlanner(), SpeechPlanner()
        for _ in range(6):
            fast.observe(50, 3.0, 2.1)
            slow.observe(50, 3.0, 3.6)
        for planner in (fast, slow):
            planner.audio_ready("a", 0.0, 4.0)
        self.assertLess(slow.rate, 1.3)
        self.assertGreater(slow.rate, fast.rate)
        self.assertLess(slow.budget(0.5, 1), fast.budget(0.5, 1))

    def test_empty_buffer_never_collapses_below_a_usable_chunk(self):
        planner = SpeechPlanner()
        self.assertGreaterEqual(planner.budget(100.0, 3), SpeechPlanner.MIN_CHARS)

    def test_sentence_pauses_stretch_only_while_the_audio_queue_is_short(self):
        planner = SpeechPlanner()
        self.assertAlmostEqual(planner.extra_pause(0.0), SpeechPlanner.MAX_EXTRA_PAUSE)
        planner.audio_ready("a", 0.0, 1.3)  # lead at t=0.2 is about half the target
        half = planner.extra_pause(0.2)
        self.assertGreater(half, 0.0)
        self.assertLess(half, SpeechPlanner.MAX_EXTRA_PAUSE)
        planner.audio_ready("b", 0.2, 10.0)
        self.assertEqual(planner.extra_pause(0.2), 0.0)

    def test_browser_schedule_corrects_the_projection(self):
        planner = SpeechPlanner()
        planner.audio_ready("a", 0.0, 2.0)
        planner.audio_ready("b", 0.5, 3.0)
        before = planner.horizon
        planner.correct("a", planner.estimates["a"] + 0.3)
        self.assertAlmostEqual(planner.horizon, before + 0.3)
        planner.correct("unknown", 99.0)
        self.assertAlmostEqual(planner.horizon, before + 0.3)

    def test_planned_chunks_keep_ahead_of_playback_when_synthesis_is_faster_than_speech(self):
        text = " ".join(f"This is sentence number {n} of the story." for n in range(14))
        planner, chunker, now, stalls = SpeechPlanner(), AdaptiveChunker(), 0.0, 0.0
        chunker.feed(text, 0.0)
        chunker.finish()
        while (chunk := chunker.take(now, planner.budget(now, chunker.count), True)):
            duration = len(chunk.text) * 0.058 + 0.4
            synth = duration * 0.85
            if chunker.count > 1:
                stalls += max(0.0, now + synth + planner.SLACK - planner.horizon)
            now += synth
            planner.observe(len(chunk.text), duration, synth)
            planner.audio_ready(chunker.count, now, duration)
        self.assertLess(stalls, 0.05)


class TrimTests(unittest.TestCase):
    def audio(self, lead, speech, tail, sr=24000):
        tone = 0.4 * np.sin(np.linspace(0, 400 * np.pi, int(speech * sr))).astype("<f4")
        return np.concatenate((np.zeros(int(lead * sr), "<f4"), tone, np.zeros(int(tail * sr), "<f4")))

    def test_caps_edge_silence_by_sentence_ending(self):
        sr = 24000
        audio = self.audio(0.2, 1.0, 0.4)
        mid = trim_silence(audio, sr, sentence_end=False)
        end = trim_silence(audio, sr, sentence_end=True)
        self.assertAlmostEqual(len(mid) / sr, 0.012 + 1.0 + 0.03, delta=0.01)
        self.assertAlmostEqual(len(end) / sr, 0.012 + 1.0 + 0.14, delta=0.01)
        self.assertGreater(np.abs(mid).max(), 0.39)

    def test_leaves_tight_and_silent_audio_alone(self):
        sr = 24000
        tight = self.audio(0.0, 0.5, 0.0)
        self.assertEqual(len(trim_silence(tight, sr, True)), len(tight))
        silent = np.zeros(1000, "<f4")
        self.assertEqual(len(trim_silence(silent, sr, True)), 1000)
