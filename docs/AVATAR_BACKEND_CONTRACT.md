# Phase 1 avatar backend contract

Implemented on `avatar/codex-core`. This worktree owns the conversation coordinator,
WebSocket router and SSE fanout; it deliberately contains no alternative STT, TTS,
chunker, AVTR worker or avatar LLM. The independent media/frontend/portrait ports
must be merged and connected to this contract before real media is available.

## Application setup and ownership (Claude)

Register a **synchronous, lightweight** runtime factory before accepting requests:

```python
from chatbot.api.app import create_app

app = create_app()
app.state.avatar_runtime_factory = lambda binding, emit: PortedMediaAdapter(binding, emit)
```

`PortedMediaAdapter` implements `chatbot.avatar.coordinator.MediaRuntime`.
`binding` supplies canonical `session_id`, authenticated `user_id`, and validated
`companion_id`. Use those IDs to load companion voice/portrait assets; chatbot's
companion profile and turn pipeline remain the only identity/prompt authorities.
The factory must not load models. `start()` is invoked only after authentication,
Origin and ownership checks, when an avatar browser connects. Package imports and
ordinary chatbot startup must not start GPU workers. Missing registration returns
an explicit WebSocket error and close code **1013**; no legacy voice fallback occurs.

The coordinator retains one binding/runtime per chatbot session. An authenticated
replacement interrupts and cleans up the old turn, then closes its browser with
**4001**, "another window took over". Old-connection cleanup cannot evict a new
binding. Disconnect releases the binding and calls `aclose()`; models may stay warm
in a shared worker pool managed by the media adapter while other bindings need them.
App shutdown closes remaining bindings. The adapter owns model pooling, portrait
switching and GPU capacity limits, including avoiding duplicate model loads.

The coordinator is process-local. Deploy this integration with **one ASGI worker**
until shared ownership/routing is provided. Sticky routing alone does not prevent
cross-worker session ownership conflicts or duplicate GPU loads.

### Required async adapter methods

| Method | Responsibility |
| --- | --- |
| `start()` | Lazily acquire workers; emit config/listening status; no LLM call. |
| `microphone(pcm: bytes)` | Consume mono little-endian float32 at 16 kHz; own VAD/STT and emit transcript/barge-in. |
| `control(message: dict)` | Handle original mic toggle, playback scheduling/telemetry and playback completion controls. |
| `begin_turn(turn_id: str)` | Set up one incremental speech turn and stamp all its output with this ID. |
| `feed_text(turn_id, text)` | Feed the **existing** chunker incrementally; never request another LLM response. |
| `end_turn(turn_id)` | Flush incremental text/chunking; return promptly, without waiting for browser playback. |
| `cancel_turn(turn_id)` | Idempotently invalidate text/TTS/render queues and late inference results; return AVTR to listening. |
| `aclose()` | Release binding resources and tasks; release/shut down workers when unused. |

Methods must yield to the event loop, honor cancellation, and never run blocking GPU
inference directly on it. Use the port's existing subprocess/thread workers. In
particular `feed_text` must feed bounded media queues, not wait for whole synthesis
or playback. The coordinator has a 128-entry / 32,768-character text queue; token
publication uses `put_nowait`, and each adapter feed/end/begin has a five-second
deadline. If the branch stalls, it cancels queued speech and sends an error for typed
chat while SSE continues; spoken turns are interrupted and save only delivery so far.
Media and browser sends apply bounded transport backpressure (two-second deadline).
Startup has a 120-second deadline; runtime close has a five-second deadline.
Adapters must also bound their own PCM/video/render queues.

### Events emitted by the adapter

Call `await emit(event)`; this is the callback passed into the factory. It may be
called from async worker pumps, **not directly from OS threads**. All speech events
require the chatbot-supplied `turn_id`; no new IDs may replace it. Segment IDs must
be globally unique (for example `f"{turn_id}:{number}"`).

```python
await emit({"type": "config", "data": {"type": "config", "sync_buffer_ms": 100}})
await emit({"type": "status", "data": {"type": "status", "value": "Listening"}})
await emit({"type": "barge_in"})
await emit({"type": "transcript", "text": "Recognized utterance"})

# In speech order, BEFORE handing that segment's audio to the browser:
await emit({"type": "segment", "turn_id": turn_id,
            "id": segment_id, "text": exact_spoken_text_with_separators})
await emit({"type": "media", "turn_id": turn_id, "data": original_binary_packet})
await emit({"type": "json", "turn_id": turn_id,
            "data": {"type": "status", "value": "Responding…"}})
# All final segments have been declared and their output dispatched:
await emit({"type": "drained", "turn_id": turn_id})
# A failed speech stage:
await emit({"type": "error", "turn_id": turn_id})
# Unscoped idle video ONLY (V packet with an empty segment ID):
await emit({"type": "idle", "data": original_idle_video_packet})
```

The internal `segment` event is a delivery ledger, not a new binary packet or a TTS
implementation. Text must reflect what is spoken after the runtime's existing
visible-text filter, include inter-chunk spaces, and arrive in speaking order.
Do not declare a segment twice or after `drained`. `drained` means no further speech
segments/output will be produced for this turn, not that the listener heard them.
The coordinator counts only known browser `playback_end` acknowledgments. It rejects
speech events from stale/cancelled IDs before sending any output. The runtime must
also discard stale results internally so they cannot change AVTR's current state.
Keep audio paired with its first speech video frame as in my_avatar. No emotion XML
is introduced. An empty STT transcript starts no chatbot turn.

## Browser integration (Gemini)

Connect to **`/api/avatar/ws`** with a normal WebSocket Origin. Configure production
origins using `AVATAR_ALLOWED_ORIGINS='["https://chat.example.com"]'`. Defaults are
`http://localhost:5173` and `http://localhost:3000`. Missing/unlisted Origins and all
query parameters are rejected. HTTP CORS does not secure this endpoint.

Send the first message within ten seconds, before sending microphone/control data:

```json
{"type":"auth","token":"<chatbot JWT>","session_id":"<UUID>","companion_id":"<companion ID>"}
```

A native client can instead use the existing `Authorization: Bearer …` header and
omit `token` from that same initial auth message. Never put credentials in the URL.
Authentication uses chatbot's existing JWT verifier. The backend validates session
ownership, companion ownership, and that the session selects that exact companion.
It sends `{"type":"ready","session_id":"…","companion_id":"…"}` after runtime
startup. The runtime may emit its original `config`/`status` first. Failed auth,
ownership or control shape closes with **1008**. Oversized packets use **1009**,
unsupported binary format **1003**, transient runtime absence/timeout **1013**,
and unexpected runtime failure **1011**.

The actual implementation inspected was
`~/dev/mike/my_avatar/python/my_avatar/app.py` and `index.html`. Preserve its wire
format, including the microphone **M prefix**:

| Direction | Bytes |
| --- | --- |
| Browser mic | `b'M' + float32_le_mono_16000Hz_PCM` |
| Server audio | `b'A' + uint16_le(id_utf8_length) + id_utf8 + uint32_le(sample_rate) + float32_le_PCM` |
| Server video | `b'V' + uint16_le(id_utf8_length) + id_utf8 + float32_le(pts_seconds) + JPEG` |

The router forwards original media packets unchanged; binary messages are limited
to 2 MiB. Continue using the audio clock for synchronized playback/video and the
configured sync buffer. Send the original controls: `mic` (`enabled`),
`segment_scheduled` (`id`, `ctx_now`, `begin`, `end`, `prev_end`), `playback_start`,
`playback_end` (`id`), `video_displayed`, `buffer_stats`, and `interruption_ack`.
`interrupt` / `barge_in` controls are also accepted for explicit user interruption.
Only send `playback_end` after natural completion, never for an interrupted segment.

On server `interrupt`, immediately fade/stop audio, invalidate the browser playback
generation and pending video/audio, then acknowledge `interruption_ack`. On **4001**,
stop mic/playback and stand down without automatic reconnection (another window owns
the avatar). Reconnection must authenticate again.

Spoken turns send `transcript`, followed by the existing SSE payload objects as JSON
(`typing`, `token`, pipeline step status, `context`, `response`/`turn_id`, `error`).
There is no literal SSE framing or `[DONE]` on the WebSocket. Use `response` to finish
a successful spoken message. On interruption there may be no final `response`;
`interrupt` clears the active speaking/typing UI. Do not POST a second chat request
for `transcript`, and do not synthesize the same response with legacy browser TTS.

Typed input continues using existing **`POST /chat/stream`** with existing auth and
session ID. Its **single** provider token stream feeds SSE and the avatar queue;
the browser must not resend typed input over the avatar WebSocket. A spoken utterance
internally invokes this same endpoint function and consumes that same turn iterator.
Both directions use the selected chatbot provider/model, companion, persistent
history, memory retrieval, context assembly and prompt trace metadata. Companion
changes invalidate an existing avatar binding; reconnect with the new selection.

## Interruption, persistence and inspection

A new turn interrupts its predecessor and waits for predecessor persistence before
reloading history. Barge-in also cancels the chatbot generation task, stops browser
playback and invalidates queued media before returning to listening. The next
utterance may then enter the pipeline. Merely cancelling an asyncio task would not
save history; explicit cleanup rolls back speculative pipeline DB state and writes
the user input, delivered assistant prefix (if any), and a fixed **system interruption
marker** sharing the same `turn_id`. No schema migration is needed. Empty/unheard
assistant text creates no assistant row. There is no LLM call for the marker.

For **spoken turns**, the provider runs concurrently with synthesis/rendering. After
provider completion, post-provider memory extraction and final persistence wait for
`drained` plus browser completion of every declared segment (180-second deadline).
Only the contiguous prefix of completed segments becomes conversational context.
On interruption, a partly heard segment is conservatively omitted; the current
protocol does not report word/sample-aligned partial delivery. The marker explicitly
records this uncertainty. The next retrieval/history load cannot invent unplayed
queued speech. Interrupted turns skip speculative post-provider memory/identity work;
the retained messages remain available to subsequent retrieval/embedding generation.

For **typed turns**, text successfully consumed from the SSE iterator is delivered
conversation content, even if its parallel speech is interrupted. Completed typed
turns persist their full text without waiting for speech playback. This avoids
changing text chat semantics or replacing text the user has read with a shorter
spoken prefix. SSE transport delivery does not prove the browser painted/read it.

Cancellation cleanup is shielded against Starlette's disconnect cancellation scope;
SSE and spoken consumers explicitly close suspended generators. A commit already in
progress is awaited before deciding on partial writes, preventing duplicate exchange
insertion when commit succeeds during cancellation. A predecessor cleanup timeout
rejects takeover rather than permitting a new turn to load stale history. Avatar persistence errors propagate
instead of silently claiming success; another avatar turn on that binding is refused
until the failure is resolved/reconnected. Database/network hangs beyond cleanup
limits remain operational failures requiring attention; no GPU/media test establishes
transaction behavior against a production database outage.

Streaming now starts/completes existing turn traces and records pipeline step timings,
provider prompt/history, routing metadata and retrieval snippets for PromptInspector.
Memory features are kept in the normal pipeline. Avatar-active turns skip the optional
LLM-based conversation title generator to avoid a second response/title generation;
existing text-only behavior retains it. The media runtime never calls an LLM. Existing
memory extraction machinery is preserved rather than replaced with avatar history.

## Legacy backend voice inventory and retirement

The integrated path imports/calls **none** of the following old voice implementations:

| Surface | Legacy implementation | Phase 1 disposition |
| --- | --- | --- |
| `POST /voice/synthesize` | `chatbot/api/voice.py`, `chatbot/voice/tts.py` (`TTSAdapter`, Kokoro/ElevenLabs and provider dispatch) | Retire once real Qwen speech + browser playback is verified after merge. |
| `POST /voice/transcribe` | `chatbot/api/voice.py`, `chatbot/voice/stt.py` (`STTAdapter`, Deepgram and provider dispatch) | Retire once real Faster-Whisper microphone turns are verified. |
| `GET/PUT /voice/preferences` | Legacy provider/model/speed/pitch fields, `VoicePreferences` | Migrate consumers to companion/runtime voice settings before removal. |
| `chatbot/voice/base.py`, `cadence.py`, `__init__.py` | Legacy interfaces/provider discovery/cadence | Remove once endpoint and frontend consumers no longer depend on them. |
| `chatbot/config.py` TTS/STT fields | Legacy provider URLs/keys/model defaults | Remove after migration, preserving avatar-specific settings from the media port. |
| `tests/test_voice.py`, `test_voice_integration.py` | Legacy behavior coverage | Replace/retire alongside the old surfaces. |

These endpoints remain mounted for existing clients while the media port is absent.
Deleting them now would remove functioning voice before its replacement is merged.
Final Phase 1 retirement must remove their router registration and obsolete imports,
frontend calls/preferences and adapters/config together after the replacement works;
**it is still an outstanding integration step**, not completed by this backend commit.

## Verification and remaining merged-system checks

`tests/test_avatar_backend.py` uses fake model stages and database boundaries to check
Origin/auth/ownership, lazy startup, takeover/disconnect, binary transport, concurrent
fanout and backpressure, stale suppression, typed/spoken session continuity, retained
memory/prompt tracing, interruption delivery, disconnect shielding, and commit races.
`tests/test_retrieval_wiring.py` protects the existing retrieval wiring. No reference
repository was edited; no GPU benchmark or full repository suite was run.

After merging: implement/register Claude's adapter, connect Gemini's authenticated
browser and playback acknowledgments, supply Pi's companion portrait/voice assets,
then verify real ASGI disconnects, actual DB commits, mic barge-in, warmed worker reuse,
A/V clock sync, and the retirement above. Capacity/load and multi-worker operation are
not established by these GPU-free tests.
