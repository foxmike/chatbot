# Avatar media runtime (`chatbot/avatar/`)

Branch `avatar/sonnet-runtime`. This is a port of my_avatar's real-time voice/avatar pipeline
(`~/dev/mike/my_avatar/python/my_avatar`, commits `c2a1906`, `da976ad`) with its LLM client and
conversation history removed. It owns **media only**: microphone → transcript, and response text →
synchronized audio + video. It makes no LLM request and has no knowledge of chatbot's database,
companions, or any provider.

## Modules

| File | Role |
|---|---|
| `speech_chunks.py` | `VisibleTextFilter`, `AdaptiveChunker`, `SpeechPlanner`, `trim_silence`, `edge_silence`. **Byte-identical to my_avatar** (a test enforces it). |
| `media.py` | `AvatarMediaEngine`: the public interface, the pull-based chunk→TTS→AVTR pipeline, interruption. |
| `session.py` | `MediaSession`: one-owner browser connection lifecycle (`da976ad`), routes engine output to the browser. Framework-neutral `MediaTransport`; `FastAPIWebSocketTransport` adapter included. |
| `protocol.py` | Binary framing (unchanged my_avatar protocol) and the event dataclasses. |
| `stt.py` | `Endpointer` (my_avatar VAD/barge-in state machine, thresholds unchanged) and `Transcriber` (faster-whisper in threads). |
| `workers.py` | `Worker`: JSON-lines subprocess driver, bounded queues, AVTR frame-drop policy, SIGTERM→SIGKILL escalation. |
| `config.py` | `MediaConfig` (+ `from_env`, `AVATAR_*` only). Known-good voice constants. |
| `metrics.py` | Per-chunk timeline / turn summary / silence-cause attribution (ported from `app.py`). |
| `worker_scripts/tts_worker.py` | Qwen3-TTS child. **Byte-identical to my_avatar.** Runs in the Qwen venv. |
| `worker_scripts/avatar_worker.py` | AVTR-1 child (my_avatar's + `portrait` command, portrait cache, EOF shutdown). Runs in the AVTR pixi env. |
| `requirements-avatar.txt` | New chatbot-side dependencies (optional). |

## Public interface

Everything is `async`; all calls are made from the FastAPI event loop. Types live in
`chatbot.avatar` (`from chatbot.avatar import ...`).

```python
engine = AvatarMediaEngine(MediaConfig.from_env(), sink=async_callback)   # or MediaSession(engine, on_event)

await engine.start()                       # on demand, idempotent, concurrent-safe; keeps models loaded
await engine.stop()                        # cancel in-flight work, release Qwen + AVTR (+ reap process groups)
engine.ready / engine.busy / engine.status()

await engine.set_portrait(path, portrait_id=None) -> PortraitResult   # never raises for a bad image
await engine.feed_microphone(pcm: bytes)   # float32le mono 16 kHz (browser's b"M" payload minus the tag)
engine.set_microphone_enabled(bool)

turn = await engine.begin_response(label=None) -> str                # engine-assigned turn id
ok   = await engine.feed_text(turn, text) -> bool                    # incremental LLM text; False = turn dead
await engine.end_response(turn)                                      # end of response text
ended = await engine.wait_turn(turn) -> TurnEnded                    # completes when speech has been *played*
await engine.cancel(turn=None)                                       # caller-initiated interruption

await engine.handle_client_message(dict)   # browser JSON: mic, segment_scheduled, playback_*, video_displayed, ...
await engine.handle_client_frame(bytes)    # browser binary (b"M" + PCM)
```

### Events (delivered to `sink`)

| Event | Meaning / what the coordinator does |
|---|---|
| `TranscriptReady(utterance_id, text, audio_seconds, endpoint_delay, stt_ms, at)` | Final transcript. **Non-empty:** start the chat turn, then `begin_response`. **Empty:** engine already returned to listening; do nothing. If you abandon a non-empty transcript without `begin_response`, call `cancel()`. |
| `Interrupted(reason, turn_id)` | `barge-in` or `disconnect`. Engine already cleared its queues/AVTR/browser; **you must stop your LLM stream** for that turn. `turn_id` is `None` if the user spoke while you were still thinking (before `begin_response`). Not emitted for a `cancel()` you requested. |
| `UserSpeechStarted(barge_in)` | Informational. |
| `TurnEnded(turn_id, status, spoken_text, error)` | `completed` / `cancelled` / `failed`. `spoken_text` = visible text received (reasoning tags removed) — use it for the history record. |
| `UiMessage(payload)` | JSON for the browser: `config`, `status`, `interrupt`. Forward as a text frame. |
| `MediaFrame(kind, segment_id, data, pts)` | Packed binary `A`/`V` frame. Forward `data` verbatim as a binary frame. |
| `Diagnostic(name, data)` | `chunk_planned` (budget, queued audio, synthesis rate, audio/char), `segment_scheduled`, `playback_start`, `first_video_displayed`, `buffer_stats`, `avtr_fps`, `avtr_interrupted`, `portrait`, `turn_summary`, `chunk_report` (per-chunk stage times, silence cause attribution, A/V drift). |

Sink exceptions are logged and swallowed; a dead browser never stops the pumps. The sink is
awaited inline, so a slow consumer applies backpressure (AVTR frames are then dropped by the
bounded worker queue exactly as in my_avatar, never the first frame of a speech segment).

### Turn contract

```
TranscriptReady ─▶ (coordinator runs its own LLM)
begin_response() ─▶ feed_text()* ─▶ end_response() ─▶ wait_turn()/TurnEnded
```

* `feed_text` awaits while >600 chars of unspoken text are queued (LLM backpressure,
  `MAX_PENDING_CHARS`). Feeding a cancelled turn returns `False` immediately — stop streaming.
* If the LLM errors mid-stream, call `end_response` (speak what exists) or `cancel`.
  A turn that is neither ended nor cancelled stays open.
* A new `begin_response` cancels a still-active previous turn.
* Segment ids are `"<engine turn seq>:<chunk n>"`; sequence numbers never repeat, so output of an
  interrupted turn is recognised as stale by number (no id set to grow or clear).

### Wire protocol (unchanged)

Server → browser binary: `b"A"+u16le len(id)+id+u32le sample_rate+float32le PCM`;
`b"V"+u16le len(id)+id+f32le pts+JPEG` (empty id = idle frame). Text: `config{sync_buffer_ms:60}`,
`status{value}`, `interrupt`. Browser → server: `b"M"`+float32le 16 kHz PCM; JSON `mic`,
`segment_scheduled`, `playback_start`, `playback_end`, `video_displayed`, `buffer_stats`,
`interruption_ack`. Audio goes to the browser with the segment's *first* video frame; frames are
paced by AVTR 500 ms ahead of real time and presented against the browser's audio clock. The
browser page (`index.html` in my_avatar) works unmodified against these frames.

### Wiring into FastAPI (for the merge)

```python
engine = AvatarMediaEngine(MediaConfig.from_env())
media = MediaSession(engine, on_event=coordinator.on_media_event)

@router.websocket("/ws/avatar")
async def avatar_ws(ws: WebSocket):
    await ws.accept()
    transport = FastAPIWebSocketTransport(ws)
    await media.attach(transport, tab=ws.query_params.get("tab", "?"))
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect": break
            if m.get("text") is not None: await media.handle_text(m["text"])
            elif m.get("bytes") is not None: await media.handle_bytes(m["bytes"])
    finally:
        await media.detach(transport)
```

`attach` closes an older window with code **4001** ("another window took over"; the page stands
down instead of reconnecting). An evicted window's own disconnect is ignored; the active
window's disconnect cancels any in-flight turn and emits `Interrupted("disconnect")`. Call
`await engine.stop()` from the FastAPI lifespan shutdown.

## Behaviour preserved from my_avatar

* **STT:** `base.en`, CPU, INT8, `vad_filter=True, beam_size=1, condition_on_previous_text=False`.
  Endpointing 550 ms silence (min 350), 30 s cap, energy VAD with adaptive noise floor, barge-in
  thresholds (0.018 / 5× floor / 240 ms while responding; 0.008 / 3× / 160 ms idle). Loading and
  inference run in threads (`asyncio.to_thread`), one transcription at a time; a test proves the
  event loop keeps ticking during inference.
* **Qwen:** `tts_worker.py` unmodified. Checkpoint `/home/mike/dev/tools/qwen/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign`,
  mode `design`, voice `"A warm, natural young adult American woman with a relaxed, expressive
  conversational voice."`, bf16 SDPA on `cuda:0`, warm-up before `ready`, runaway cap
  `0.085·len(text)+1.6 s` with one regeneration, stdout JSON-lines with base64 float32 PCM.
  Interruptions never restart Qwen; an inference already running finishes and its result is dropped.
* **Chunking/planning:** `AdaptiveChunker` pull model (first chunk 38 chars, ≤150), `SpeechPlanner`
  (learned RTF and audio/char, 250 ms slack, 1.1× safety, ≤8 s audio, extra sentence pauses ≤260 ms),
  playback feedback via `segment_scheduled` → `planner.correct`, `MAX_AUDIO_CHUNKS=3` bounded PCM
  queue and playback slots, `MAX_LEAD_SECONDS=10`, `URGENT_LEAD=0.8`, `FIRST_CHUNK_WAIT=0.3`.
* **Silence trimming:** 12 ms lead, 30 ms (mid-sentence) / 140 ms (sentence end) tail, 6 ms fades.
* **AVTR:** 16 kHz polyphase resample (`resample_poly(audio, 16000, sr)`, in a thread), 25 fps,
  `AVTR_RENDER_LEAD_MS=500`, 640×360 JPEG q78, `plain_white` background, listen packets only while
  idle, bounded queues (AVTR 64 frames, TTS 4 messages), existing TensorRT engines untouched.
* **Sync:** `AV_SYNC_BUFFER_MS` default 60 announced in the `config` message.

### Deliberate differences

| Change | Why |
|---|---|
| LLM stream, history, OpenRouter code removed | Codex's coordinator owns the turn. |
| AVTR worker takes the portrait via a `portrait` command instead of argv | Required for per-character switching. |
| AVTR worker exits when stdin closes | Original reader thread ended silently and the render loop ran forever if the parent died. |
| `stop()` escalates stop → SIGTERM → SIGKILL on the worker's process group; workers start in their own session | "Shutdown must cleanly release child processes." |
| Stale output is detected by monotonic turn number | Replaces `interrupted_ids` (a set that was cleared at 100 entries, briefly re-admitting stale ids). |
| Workers/STT start concurrently (original: Whisper first, then workers) | Faster cold start; independent resources. |
| Optional `nvidia-smi` sampler off by default (`AVATAR_GPU_SAMPLING=true`) | Diagnostic only. |

## Configuration

Isolated from `chatbot.config` (importing `chatbot.avatar` does not import it; a test asserts this).
`MediaConfig()` defaults equal the known-good my_avatar values; `MediaConfig.from_env()` reads only
`AVATAR_*`: `AVTR_PATH`, `QWEN_PATH`, `QWEN_MODEL`, `QWEN_VOICE`, `STT_MODEL`, `STT_DEVICE`,
`STT_COMPUTE_TYPE`, `STT_LANGUAGE`, `ENDPOINT_SILENCE_MS`, `MAX_UTTERANCE_SECONDS`,
`BARGE_IN_ENABLED`, `SYNC_BUFFER_MS`, `PORTRAIT`, `GPU_SAMPLING`. No secrets are involved (the engine
makes no network calls).

## Dependencies

New chatbot-side (optional, imported lazily; `chatbot/avatar/requirements-avatar.txt`):
`faster-whisper>=1.2,<2`, `scipy>=1.15,<2`, `numpy>=2,<3` (already present). Python 3.12 resolution was
checked with `uv pip compile --python-version 3.12` (faster-whisper 1.2.1, ctranslate2 4.8.4,
onnxruntime 1.31.1, scipy 1.18.1). **The shared chatbot venv was not modified**, and `pyproject.toml`
was deliberately left alone to avoid merge conflicts; on merge, add these as an `avatar` optional extra.
`start()` raises a clear error if scipy/faster-whisper are missing instead of failing mid-conversation.
Qwen and AVTR keep their own environments; nothing was reinstalled.

## Character portraits: findings

* `Pipeline.from_artifacts` returns only the avatars named at construction, but `process_chunk(avatar, …)`
  takes the avatar as an argument, and `AvatarLoader.load(path, avatar_id=…)` builds a new immutable
  `Avatar` from an image using **only AVTR's ONNX face models** (face detection, 106/203 landmarks,
  appearance and motion extractors). The TensorRT engines (HuBERT, AVTR-1 encode/decode, warp,
  decoder, stitch, MODNet) are shared and are neither rebuilt nor reloaded. Portrait switching is
  therefore supported without engine work.
* The worker captures the `AvatarLoader` that `from_artifacts` builds (no second set of ONNX sessions),
  keeps up to 4 loaded avatars (~25 MB CUDA each) keyed by `portrait_id`, and resets the autoregressive
  state on every switch (it belongs to one avatar). Switching back to a cached portrait costs only the reset.
* **Latency is not measured** (no GPU runs were allowed). A first-time load runs five ONNX models plus
  CPU cropping on the render thread, so idle frames pause while it runs; expect it to be well above 60 ms.
  Do not promise instant switching on first selection. Recommended: call `set_portrait` when the user
  selects/opens a character (not at first utterance), and pre-warm recently used characters.
* Invalid portraits: pre-flight (exists, 0 < size ≤ 25 MB, PNG/JPEG/WebP/BMP magic) in-process;
  decode/size/channel checks and face detection in AVTR. Every failure returns
  `PortraitResult(ok=False, error=…)` and **leaves the previous avatar active**. Refused while a response is active.
* Behaviour inherited from AVTR's loader: 3-channel images keep their own background (matting skipped);
  4-channel images use MODNet over `plain_white`. Images are **stretched** to 1280×720 regardless of aspect
  ratio (the existing 626×417 JPG already is) — Pi should supply 16:9 (or crop/letterbox) portraits.
  A sidecar `<stem>.pbmask.png` is intentionally ignored (the worker converts to a private temp PNG,
  as my_avatar did).
* `portrait_id` defaults to a hash of path+mtime+size; Pi may pass a stable id (e.g. companion id + image hash).

## Tests

`python -m pytest tests/avatar_media -q` — 84 tests, no GPU, no Qwen/AVTR, no network: ported chunker,
planner and trimming suites; engine on fake workers (ordering, bounding, overlap, wire bytes, barge-in,
stale Qwen/AVTR output suppression, next-turn correctness, error propagation, backpressure, portraits,
lifecycle/restart/stop); real stub child processes for `Worker` (start, restart, crash, timeout,
SIGTERM/SIGKILL escalation, frame drop); session/connection lifecycle including a real Starlette
WebSocket; AVTR worker helpers against fake `cv2`/`avtr1_renderer`; byte-identity of the proven modules.

## Performance risks and remaining work

* **Unverified on hardware.** Baselines (Qwen RTF ≈ 0.86–0.90 alongside AVTR, ~25 fps AVTR, near-gapless
  audio, A/V drift ≈ ±35 ms) belong to the unchanged algorithms and workers, but this port has not been run
  against real Qwen/AVTR/Chrome. First integration run should use my_avatar's `bench_pipeline.py`
  methodology against the merged server, one GPU consumer at a time.
* **Event-loop contention:** the engine runs in chatbot's loop next to chat/streaming. The 25 fps frame pump
  (base64 decode + `send_bytes`), PCM resample and Whisper are off-loop or cheap, but a busy loop delays the
  `0.05 s` planner tick and frame delivery. Watch `Frame delivery interruption` logs and the `avtr_fps` diagnostic.
* **Sink back-pressure:** an `on_event` that awaits slow work stalls the AVTR frame pump. Keep it to queue puts.
* **Shared GPU:** Qwen + AVTR + whatever else chatbot loads (e.g. embeddings) compete; the RTF baseline assumes only these two.
* **Portrait switch cost** (above) and AVTR ONNX sessions bound to the worker's CUDA stream (loader runs on the
  worker main thread, same as rendering, so this is satisfied today).
* **Coordinator integration still to do (Codex / router owner):** instantiate one engine in the app lifespan,
  call `engine.stop()` on shutdown; feed `TranscriptReady` into the chat turn and pipe the provider's token
  stream into `feed_text`; stop that stream on `Interrupted`; persist `TurnEnded.spoken_text`; call `set_portrait`
  with Pi's cached path on character select; mount the WebSocket as shown above; serve the browser page
  (Gemini). `AVATAR_*` env vars and the optional extra need adding to deployment config.
