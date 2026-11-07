# Avatar media runtime (`chatbot/avatar/`)

This is a port of my_avatar's real-time voice/avatar pipeline
(`~/dev/mike/my_avatar/python/my_avatar`, commits `c2a1906`, `da976ad`) with its LLM client and
conversation history removed. It owns **media only**: microphone → transcript, and response text →
synchronized audio + video. It makes no LLM request and has no knowledge of chatbot's database,
companions, or any provider.

## Modules

| File | Role |
|---|---|
| `speech_chunks.py` | `VisibleTextFilter`, `AdaptiveChunker`, `SpeechPlanner`, `trim_silence`, `edge_silence`. **Byte-identical to my_avatar** (a test enforces it). |
| `sentence_chunks.py` | `SentenceChunker` (natural mode) and `make_chunker(mode)`. Kept out of `speech_chunks.py` so the proven chunker stays byte-identical. |
| `media.py` | `AvatarMediaEngine`: the public interface, the pull-based chunk→TTS→AVTR pipeline, interruption. |
| `session.py` | `MediaSession`: one-owner browser connection lifecycle (`da976ad`), routes engine output to the browser. Framework-neutral `MediaTransport`; `FastAPIWebSocketTransport` adapter included. |
| `protocol.py` | Binary framing (unchanged my_avatar protocol) and the event dataclasses. |
| `stt.py` | `Endpointer` (my_avatar VAD/barge-in state machine, thresholds unchanged) and `Transcriber` (faster-whisper in threads). |
| `workers.py` | `Worker`: JSON-lines subprocess driver, bounded queues, AVTR frame-drop policy, SIGTERM→SIGKILL escalation. |
| `config.py` | `MediaConfig` (+ `from_env`, `AVATAR_*` only). Known-good voice constants. |
| `metrics.py` | Per-chunk timeline / turn summary / silence-cause attribution (ported from `app.py`). |
| `worker_scripts/tts_worker.py` | Qwen3-TTS child. **Byte-identical to my_avatar.** Runs in the Qwen venv. |
| `worker_scripts/avatar_worker.py` | AVTR-1 child (my_avatar's + `portrait` command, portrait cache, EOF shutdown). Runs in the AVTR pixi env. |
| `runtime.py` | `AvatarRuntimeHost` + `MediaAdapter`: connects the engine to Codex's coordinator/router (`app.state.avatar_runtime_factory`). |

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

## TTS speech modes

How the reply text is cut for Qwen3-TTS. Only generated speech is affected: microphone, STT,
endpointing, VAD and the LLM text stream are not.

| Mode | Behaviour |
|---|---|
| `natural` (default) | One TTS request per **complete sentence** (`SentenceChunker`). No size limit, no clause/word cuts, no first-chunk timeout, no playback-driven cuts. A paragraph break also ends a unit; leftover text is flushed when the response finishes. Abbreviations (`Dr.`, `e.g.`, `p.m.`), initials, decimals, URLs, list markers and ellipses are not treated as sentence ends; closing quotes, brackets and markdown emphasis stay with their sentence. A full stop followed only by whitespace waits for the next character (it could be a lowercase continuation). |
| `fast` | The existing adaptive chunking and `SpeechPlanner`, unchanged (38/28/150 chars, 0.3 s first-chunk wait, 0.8 s urgent lead, 10 s max lead, 3 queued chunks). |

* The LLM text still streams to the UI immediately; only the TTS branch waits. Natural mode does
  not apply the engine's pending-text backpressure (it would wait for a sentence end that needs
  the text it is throttling). Later sentences still synthesize while earlier ones play, bounded by
  the existing `MAX_AUDIO_CHUNKS` / `MAX_LEAD_SECONDS` limits.
* A sentence Qwen cannot synthesize fails the turn with the worker's error; it is never split or
  retried in `fast` mode.
* The mode is per avatar connection (`AvatarBinding.speech_mode`). The popup sends the saved choice
  as `tts_mode` in the `auth` handshake and `{"type": "tts_mode", "mode": ...}` when the dropdown
  changes. Invalid values are rejected (handshake: `error` frame, then close 1008; live message:
  `error` frame, mode unchanged). `begin_response(speech_mode=...)` captures the mode when a turn
  starts, so a change applies from the next turn, for typed and spoken turns alike.
* Turn diagnostics (`turn_summary`, `chunk_planned`) carry `speech_mode`; the summary line adds
  average characters per chunk, sentence-boundary vs mid-sentence cuts and playback gaps next to
  the existing first-token / first-sentence / first-chunk / first-PCM / first-playback / RTF figures.

## Voice Library (saved voices)

Who speaks is a saved voice: a canonical **reference recording + its transcript**. Every
conversational sentence is synthesized by **Qwen3-TTS-12Hz-1.7B-Base** with
`generate_voice_clone(voice_clone_prompt=...)`, the prompt built once per voice by
`create_voice_clone_prompt(ref_audio, ref_text)` (full ICL cloning, never embedding-only).
Voices are created in two ways, both ending in the same reference + transcript:

* **Design** — **Qwen3-TTS-12Hz-1.7B-VoiceDesign** renders candidates from a description;
  the user saves one, and that exact recording (moved, never regenerated) is the reference.
* **Clone** — an uploaded recording (validated, decoded in memory with PyAV, normalized to
  24 kHz mono PCM WAV) plus a user-reviewed transcript (faster-whisper may suggest one).

| Piece | Where |
|---|---|
| Qwen worker | `worker_scripts/voice_worker.py` — Base loaded at start and kept warm; VoiceDesign loaded only for design previews and released after `AVATAR_DESIGN_IDLE_SECONDS` (or on save/cancel). One thread executes all model work in order, so GPU operations never overlap. Reuses the proven runaway guard from the byte-identical `tts_worker.py`. |
| Prompt cache | In the worker's memory, keyed by (voice id, fingerprint = sha256(reference sha256 + transcript)), LRU of 8. A changed reference/transcript has a new key; `forget` drops a deleted/edited voice. Nothing serialized: after a restart prompts are rebuilt from the files. |
| References | Read only from inside `VOICE_LIBRARY_DIR`, passed to Qwen as `(waveform, sr)` (never a path/URL, so Qwen's URL loader is never used). |
| Persistence | `voices` table (metadata; migration `e7a4c2d9f1b3`), files under `VOICE_LIBRARY_DIR/library/<voice_id>/reference.wav`. Candidates live in `previews/<user_id>/<preview_id>/` until saved, cancelled or expired. |
| Selection | Per speech turn, `MediaAdapter.begin_turn` resolves: conversation override (`sessions.voice_id`) → companion default (`companions.voice_id`) → built-in default voice. An unusable selected voice fails the turn's speech with an explicit error; it never falls back. The resolved voice is fixed for the whole turn (`_Turn.voice`). |
| Built-in default | Explicit initialization: `python -m chatbot.voices.init_default` (or admin `POST /voices/default/initialize`) renders one VoiceDesign reference of `AVATAR_QWEN_VOICE`. It is a new rendering, not bit-identical to earlier per-sentence output. |
| API | `chatbot/api/voices.py`: `GET /voices`, `POST /voices/previews/design`, `POST /voices/references` (upload), `POST /voices/previews/{id}/sample`, `GET /voices/previews/{id}[/audio/{reference,sample}]`, `DELETE /voices/previews/{id}`, `POST /voices`, `GET/PATCH/DELETE /voices/{id}` (delete refuses a voice in use unless `replacement_voice_id` / `clear_session_overrides`), `GET /voices/{id}/audio`, `POST /voices/{id}/speak`, `GET/PUT /voices/sessions/{session_id}`. |

Previews run on the same persistent worker (only the TTS worker is started for them, never
AVTR) and are refused (409) while the avatar is speaking. Interruptions never cancel them.

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

`faster-whisper`, `scipy`, `numpy` and `pillow` are regular dependencies in `pyproject.toml`
(Python 3.12; faster-whisper 1.2.1, ctranslate2 4.8.4, scipy 1.18.1 verified). They are imported lazily:
`start()` raises a clear error if scipy or faster-whisper is missing. Qwen and AVTR keep their own
environments; nothing was reinstalled.

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

## Measured on real hardware (RTX 4090, one GPU consumer)

Through the full backend (real LLM, Qwen, AVTR): Qwen RTF 0.85-0.92 while AVTR renders, AVTR 25.0 fps
(render compute 8-10 ms per 200 ms step), zero inter-segment gaps over multi-segment answers, STT 311 ms
for a 2 s utterance, barge-in clears AVTR's speech queue in 5-32 ms. Cold start (Whisper + Qwen + AVTR +
first portrait) about 7 s with warm disk caches; reconnecting a popup to the warm engine about 0.2 s.
A first-time portrait load took 540-680 ms. Qwen occasionally runs away on a chunk (about 1 in 10 here);
the length guard regenerates it once, which stalls that chunk for several seconds (RTF 2.7 for that chunk).

## Remaining risks

* The engine shares chatbot's event loop; a slow `on_event`/websocket consumer stalls the frame pump.
* Qwen + AVTR assume the GPU is otherwise idle; other GPU users will lower the RTF.
* A barge-in on a *typed* turn cancels that turn's generation task (coordinator design), which ends its
  SSE stream early; the delivered prefix and an interruption marker are persisted.
