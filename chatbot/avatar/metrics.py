"""Logging helpers and the per-chunk timeline/diagnostic reports ported from my_avatar."""

from __future__ import annotations

import logging
import time

_logger = logging.getLogger("chatbot.avatar")


def log(area: str, message: str) -> None:
    _logger.info("[%s] %s", area, message)


def elapsed(start: float, end: float | None = None) -> str:
    return f"{1000 * ((end or time.monotonic()) - start):.0f}ms"


def chunk_report(timeline: dict[str, dict], turn_key: str, speech_end: float) -> list[dict]:
    """Per-chunk stage times (ms after `speech_end`) and the cause of each inter-chunk silence."""
    rows = []
    number = 1
    while (t := timeline.get(f"{turn_key}:{number}")):
        rows.append((f"{turn_key}:{number}", t))
        number += 1

    def ms(a, b):
        return 1000 * (b - a) if a is not None and b is not None else None

    report = []
    previous = None
    for segment_id, t in rows:
        row = {"id": segment_id, "chars": t.get("chars"), "reason": t.get("reason"),
               "at": {k: ms(speech_end, v) for k, v in t.items() if isinstance(v, float)},
               "audio_s": t.get("duration"), "synth_ms": 1000 * t["generation"] if "generation" in t else None,
               "llm_ms": ms(t.get("text_started"), t.get("text_ready")),
               "hold_ms": ms(t.get("text_ready"), t.get("text_emit")),
               "tts_queue_ms": ms(t.get("text_emit"), t.get("synth_start")),
               "tts_idle_ms": ms(t.get("tts_idle_since"), t.get("tts_got")),
               "synth_wall_ms": ms(t.get("synth_start"), t.get("synth_end")),
               "handoff_ms": ms(t.get("synth_end"), t.get("avatar_sent")),
               "avatar_ms": ms(t.get("avatar_sent"), t.get("first_frame")),
               "avtr_ahead_ms": ms(t.get("first_frame"), previous["b_end"]) if previous and "b_end" in previous else None,
               "drift_ms": t.get("drift_ms"), "retried": t.get("retried"),
               "silence_ms": None, "pause_ms": None, "raw_edges_ms": None, "boundary": None, "causes": {}}
        if previous and "b_begin" in t and "b_end" in previous:
            window = (previous["b_end"], t["b_begin"])
            row["silence_ms"] = max(0.0, ms(*window))
            row["boundary"] = "sentence" if previous.get("sentence_end") else "mid-sentence"
            if "trail_sil" in previous and "lead_sil" in t:
                row["pause_ms"] = row["silence_ms"] + 1000 * (previous["trail_sil"] + t["lead_sil"])
                row["raw_edges_ms"] = 1000 * (previous.get("raw_trail_sil", 0) + t.get("raw_lead_sil", 0))
            chain = [("llm_idle", window[0], t.get("text_started")),
                     ("llm_text", t.get("text_started"), t.get("text_ready")),
                     ("chunker_hold", t.get("text_ready"), t.get("text_emit")),
                     ("tts_queue", t.get("text_emit"), t.get("synth_start")),
                     ("tts_synthesis", t.get("synth_start"), t.get("synth_end")),
                     ("queue_handoff", t.get("synth_end"), t.get("avatar_sent")),
                     ("avatar_sync", t.get("avatar_sent"), t.get("first_frame")),
                     ("browser_transport", t.get("first_frame"), t.get("b_scheduled_at")),
                     ("playback_buffer", t.get("b_scheduled_at"), t.get("b_begin"))]
            for name, a, b in chain:
                if a is None or b is None:
                    continue
                overlap = min(b, window[1]) - max(a, window[0])
                if overlap > 0.002:
                    row["causes"][name] = 1000 * overlap
        report.append(row)
        previous = t
    return report


def format_chunk_row(row: dict) -> str:
    def f(v):
        return "-" if v is None else f"{v:.0f}"

    at = row["at"]
    line = (f"{row['id']} {row['chars']}ch ({row['reason']}); text {f(at.get('text_started'))}→{f(at.get('text_ready'))}"
            f" (LLM {f(row['llm_ms'])}, hold {f(row['hold_ms'])}); TTS queue {f(row['tts_queue_ms'])}"
            f" (worker idle {f(row['tts_idle_ms'])}); synth {f(at.get('synth_start'))}→{f(at.get('synth_end'))}"
            f" (model {f(row['synth_ms'])}, audio {row['audio_s'] or 0:.2f}s); handoff {f(row['handoff_ms'])};"
            f" AVTR {f(row['avatar_ms'])} (frames {f(row['avtr_ahead_ms'])} before prior audio ends); browser audio {f(at.get('b_begin'))}→{f(at.get('b_end'))}")
    if row["drift_ms"] is not None:
        line += f"; A/V drift {row['drift_ms']:.0f}ms"
    if row["silence_ms"] is not None:
        causes = ", ".join(f"{k} {v:.0f}" for k, v in sorted(row["causes"].items(), key=lambda x: -x[1]))
        line += f"; SILENCE BEFORE {row['silence_ms']:.0f}ms" + (f" [{causes}]" if causes else "")
        if row["pause_ms"] is not None:
            line += f"; {row['boundary']} pause {row['pause_ms']:.0f}ms"
    return line


def format_summary(m: dict, gpu_samples=()) -> str:
    """One-line turn summary. `m["speech_end"]` is the turn's time origin."""
    base = m["speech_end"]
    fields = [f"TURN {m['turn']}{' INTERRUPTED' if m.get('interrupted') else ''}"]
    if m.get("endpoint_delay") is not None:
        fields.append(f"endpoint {m['endpoint_delay']*1000:.0f}ms")
    if m.get("stt_start") is not None:
        fields.append(f"endpoint→STT start {elapsed(base, m['stt_start'])}")
    if m.get("stt_end") is not None:
        fields.append(f"STT processing {elapsed(m['stt_start'], m['stt_end'])}")
    for key, label in (("first_token", "first LLM token"),
                       ("first_sentence", "first complete sentence"),
                       ("first_speech_chunk", "first speech chunk"),
                       ("first_audio", "first PCM"),
                       ("first_playback", "browser playback start"),
                       ("first_video_generated", "first video generated"),
                       ("first_video_displayed", "first video displayed")):
        if key in m:
            fields.append(f"{label} {elapsed(base, m[key])}")
    if m.get("audio_duration"):
        fields.append(f"TTS generation {m['tts_total']*1000:.0f}ms / audio {m['audio_duration']*1000:.0f}ms "
                      f"(RTF {m['tts_total']/m['audio_duration']:.2f})")
    fields.append(f"chunks {m['chunks']}")
    samples = [(memory, utilization) for at, memory, utilization in gpu_samples if at >= base]
    if samples:
        fields.append(f"GPU {min(x[0] for x in samples)}–{max(x[0] for x in samples)}MiB; "
                      f"peak utilization {max(x[1] for x in samples)}%")
    return "; ".join(fields)
