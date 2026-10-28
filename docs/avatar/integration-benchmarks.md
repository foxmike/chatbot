# Integrated avatar benchmark contract

Comparison target: standalone `my_avatar` (read-only, unmodified) vs the
integrated chatbot avatar subsystem. Ground rules:

* Never run concurrent GPU benchmarks — cases run sequentially with a
  cooldown; only one of {Qwen, AVTR, integrated app} drives the GPU at a
  time.
* Never alter the standalone implementation; it is the reference baseline.
* Every reported number must come from an observed event or timestamp.
  No extrapolation, no estimates, no filler.

## Tooling available now

`scripts/avatar_benchmark_comparison.py` drives the JSON-lines worker
protocol my_avatar's persistent workers speak (`tts_worker.py`,
`avatar_worker.py`) and reports, per text case (short/medium/long, same
chunking as `my_avatar/benchmark.py`):

* first TTS PCM (request → first `audio` message),
* per-chunk TTS RTF (`generation`/`audio duration` from the worker),
* first AVTR speech frame after first PCM,
* inter-chunk gaps (`speech_end(n)` → next PCM minus audio duration n),
* AVTR FPS (worker `fps` messages),
* per-segment pts span (proxy for frame pacing),
* interruption latency (`interrupt` → `interrupted` ack, mid-speech).

It supports an `--tts-cmd/--avtr-cmd` integrated target so the same
harness measures both implementations once the integrated media runtime
exists. Until then it runs the standalone baseline only and refuses to
fabricate a comparison.

## What the worker harness cannot measure (integration contract)

These require the full conversational stack and must be measured at the
integrated app's WebSocket, using the same event/timestamp discipline as
`my_avatar/app.py::Timeline` (monotonic clock, base = turn start):

| Metric | Definition (integrated) | Standalone equivalent |
|---|---|---|
| Endpoint detection | mic PCM in → final barge-in-eligible endpoint decided (Faster-Whisper segment end / VAD) | `AdaptiveChunker` endpoint in `app.py` mic path |
| STT time | endpoint → transcript available | whisper stage in `conversation.turn` |
| Context retrieval overhead | transcript ready → prompt assembled (memory/companion retrieval DB time) | n/a standalone (no retrieval) — **this is the integration delta to isolate** |
| First LLM token | prompt sent → first streamed delta | `metrics["first_token"]` in `app.py` |
| First TTS PCM | first speech chunk queued → first PCM bytes | `metrics["first_audio"]` / `benchmark.py` |
| Browser playback start | first audio bytes *received by the browser* → `AudioContext` start (instrument in the client; wall-clock from server alone overstates it) | `metrics["first_playback"]` |
| Qwen RTF | synthesis time / audio seconds, per chunk | `benchmark.py` RTF lines |
| Inter-chunk gaps | wall gap between audible end of chunk n and chunk n+1 (server timestamps corrected by `sync_buffer_ms`) | frame/audio pacing logs |
| AVTR FPS | worker `fps` messages, min/median/max | `benchmark.py` fps lines |
| A/V drift | presentation pts of speech frames vs the audio clock they are synchronized to (browser `AudioContext.currentTime` at render) | pts/audio-clock sync in `app.py::send_media` |
| Interruption latency | user speech detected during playback → playback actually stops + avatar returns to idle | `interrupt` handling, `interrupt_requested_at` in `app.py` |

## Procedure for the final comparison

1. Machine idle; no other GPU process. Verify with `nvidia-smi` before and
   after each case (the standalone benchmark's GPU sampling pattern).
2. Run the standalone baseline:
   `python scripts/avatar_benchmark_comparison.py --cases short medium long`
   (uses `my_avatar/.env` paths read-only; ≥3 repetitions per case, report
   median and spread — do not average away cold-start variance; run a
   warmup case first and discard it).
3. Run the integrated target with the same cases once the merged media
   runtime exposes the worker contract:
   `python scripts/avatar_benchmark_comparison.py --tts-cmd <...> --avtr-cmd <...>`
   plus the WS-level metrics above captured by an instrumented client
   (record raw event timestamps to JSON via `--json-out`; derive metrics in
   the report script, never by hand).
4. Compare like for like: same text chunks, same voice model, same
   portrait. The portrait bytes must come from `PortraitService`
   (`PreparedPortrait.png_bytes`) for the integrated run and the same
   source image file for the standalone run.
5. Attribution: differences in "context retrieval overhead" and
   "first LLM token" belong to the chatbot stack, not to AVTR/Qwen; do not
   fold them into media-pipeline comparisons.

## GPU sharing note (from my_avatar's README, to verify during integration)

Measured Qwen3-TTS synthesis cost is ~0.7 s/audio-second alone and ~0.9 s
with AVTR sharing the GPU (time-slicing, same cost whether AVTR is idle or
speaking). The integrated comparison must therefore measure both engines
co-resident exactly as they will run in production, and must not report
single-process numbers as the integrated figure.