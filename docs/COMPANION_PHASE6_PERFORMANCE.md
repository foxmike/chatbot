# Companion Evolution — Phase 6.2 Expressive Dialogue and Performance Contract

**Status:** DESIGN, awaiting approval of §12. No production code. Implemented by a later task.
**Requirements:** P03, P06, A12, A13, A15 (A10 as constraint). Baseline: `main` at `59e52f7`.

## 0. What was verified (and what that means)

| Fact (source) | Consequence |
|---|---|
| All three modes use one provider-token loop: `_stream_provider_tokens` → SSE `token` events **and** `coordinator.publish` → `runtime.feed_text`; `ctx.provider_response` = the joined raw text, `final_response = provider_response` (`RespondStep`), which is what is stored, extracted and replayed as history (`api/streaming.py`, `core/turn.py`) | Metadata must be split off **before** a token is emitted or `provider_response` is set; there is no later cleanup point |
| Standard chat is one `provider.complete()` (`CallProviderStep`) | Same grammar must also parse a complete text |
| `VisibleTextFilter` already withholds `<think>` tags across token boundaries before TTS (`avatar/speech_chunks.py`) | Precedent for a stateful stream filter; no new streaming architecture needed |
| Speech path: `speak` = `{id, text, voice}` only; Qwen Base clone `generate_voice_clone(text, language, voice_clone_prompt, max_new_tokens)` (`avatar/worker_scripts/voice_worker.py`; installed `qwen_tts` signature) | The conversational Qwen path has **no** emotion, style, rate or instruction parameter |
| Engine already post-processes PCM: `trim_silence`, planner `extra_pause` silence appended after sentence ends (`avatar/media.py`) | Pause is controllable **by Chatbot**, independent of any TTS engine |
| AVTR receives 16 kHz audio only (`avatar/protocol.py`, `avatar_worker.py`) | No expression input; performance reaches the avatar only through audio |
| Fish Audio: **no adapter, config or dependency in the repo.** An external experiment (`~/dev/tools/fish/run_drama3_ditto_female.sh`) posts `{model, voice, input, response_format}` to OpenRouter `/audio/speech`; its script text uses inline natural-language tags (`[slightly amused, a touch of dry sarcasm] ...`), as the Fish README documents | Fish control is in-text tags at the engine input; no rate/emotion request fields exist in that call |

## 1. Canonical structure (`chatbot/core/performance.py`)

```python
@dataclass(frozen=True, slots=True)
class Performance:                       # every field optional; all empty => None, never stored
    emotion: tuple[str, ...] = ()        # 0-2 free-text labels (<=24 chars, <=3 words); two labels may conflict
    intensity: Literal["low","mid","high"] | None = None   # of the emotion; dropped if no emotion
    tone: str | None = None              # <=40 chars: "dry", "hushed"
    style: str | None = None             # <=40 chars delivery/inflection: "trailing off", "whisper"
    pace: Literal["slow","fast"] | None = None   # absent = natural
    emphasis: tuple[str, ...] = ()       # <=3 words that occur verbatim in the segment text
    pause: Literal["short","long"] | None = None # silence after this segment

@dataclass(frozen=True, slots=True)
class DialogueSegment:
    index: int                           # required
    text: str                            # required: non-empty, one line, trimmed
    performance: Performance | None = None

@dataclass(frozen=True, slots=True)
class Dialogue:
    segments: tuple[DialogueSegment, ...]
    @property
    def text(self) -> str: ...           # visible dialogue: segment texts joined by one space
```

Emotion is a list of free labels, not a positive/negative axis; nuance is two labels ("hesitant", "relieved"). No vocabulary is enforced (both Fish tags and Qwen `instruct` are natural language), and no field is required of any segment.

## 2. Wire format from the model (one generation, no extra call)

Dialogue is plain lines. After a line the model **may** add one performance line starting with `@@ ` and a JSON object with short keys `emo`, `int`, `tone`, `style`, `pace`, `emph`, `pause`. Plain delivery needs no line. Dialogue is never inside JSON (no escaping errors, nothing to decode while streaming).

```
Wait, you actually got it running? That's wonderful!
@@ {"emo":["excited"],"int":"high","pace":"fast","emph":["actually"]}
Hmm... I wasn't sure it would hold up under load, to be honest.
@@ {"emo":["hesitant","relieved"],"int":"low","style":"trailing off","pace":"slow","pause":"long"}
Well. Next time, maybe we don't test in production.
@@ {"emo":["amused"],"tone":"dry","style":"deadpan"}
```

Parsed: 3 segments; visible chat (SSE, stored, history, extraction): `Wait, you actually got it running? That's wonderful! Hmm... I wasn't sure it would hold up under load, to be honest. Well. Next time, maybe we don't test in production.` Performance is **trailing** so the first visible token is never delayed by metadata.

## 3. Streaming parser (`DialogueStreamParser`)

Pure, synchronous, character-driven; `feed(text) -> events`, `finish()`, `cancel()`, `expire(now)`. Events: `Delta(text, segment_index)`, `SegmentComplete(DialogueSegment)`, `Dropped(reason)`.

- **Line start:** if the first character is not `@`, the line is dialogue and its characters are emitted at once. If it is `@`, hold until the second character is known (`@@` → performance line; anything else → release both as dialogue). Normal dialogue is never buffered.
- **Dialogue:** deltas stream immediately (unchanged first-text latency). A segment starting after earlier text emits one leading space with its first character. Newlines are never emitted; blank lines are skipped.
- **Performance line:** buffered to the newline (cap 240 chars), parsed, attached to the segment just completed.
- **Boundaries:** a segment ends at the first newline after non-empty text. It is released to speech when (a) its trailer parses, (b) the next line starts as dialogue, (c) the stream finishes, or (d) `expire` fires `performance_grace_ms` (proposed 300) after the newline. Token splits inside `@@`, JSON or newlines change nothing: output depends only on the character sequence.
- **Plain fallback:** a response with no `@@` lines is plain dialogue: segments with no performance. A model that ignores the format yields today's behaviour.
- **Batch:** `parse_dialogue(text)` = one `feed` + `finish`; same grammar for standard chat.

## 4. Partial and malformed output

Only metadata is ever dropped; dialogue is never lost. Every drop records a reason in diagnostics.

| Input | Result |
|---|---|
| Invalid JSON, non-object, line over 240 chars | line dropped, segment plain (`bad_json`, `too_long`) |
| Unknown keys, wrong types, bad enums | that field dropped, rest kept |
| `emph` word not in the segment text | word dropped |
| Trailer with no preceding segment (leader/orphan) | dropped (`orphan`) |
| Trailer after the grace expired | dropped (`late`); segment already spoken plain |
| Stream ends or cancels mid-trailer | partial line dropped; nothing visible |
| `@@` not at line start | ordinary dialogue text (known limitation, measured as leak rate) |
| More than 6 performance lines in a reply | the rest ignored (`over_limit`) |
| `Provider stream error: ...` text | bypasses the parser exactly as today |

Cancellation/barge-in: deltas already emitted stay as delivered; the open segment never completes; unplayed segments and all performance are discarded; persistence uses the existing heard-text rule (`AvatarTurn.heard_text` / `delivered_text`). No parser state outlives the turn (no timers once `finish`/`cancel` runs).

## 5. Dialogue separation and mode consistency

- **One canonical representation:** `Dialogue`. `ctx.provider_response` (hence `final_response`, stored messages, history, extraction, titles, export) is always `Dialogue.text`. Performance is `ctx.metadata["performance"]`: per segment `{index, start, end, performance}` offsets into the dialogue. It is not stored with messages.
- **Standard:** `parse_dialogue` on the full text. **SSE:** deltas become `token` events; the stream/`done` payloads are unchanged. **Spoken:** the same deltas stream to the screen; speech receives completed segments (§10).
- Equivalence: the same raw output gives the same `Dialogue` in all three modes and under any chunking.
- Performance fields are delivery descriptors, capped in length; no reasoning is requested, parsed or kept. Visible inline tags are never the interface: markers exist only in the provider stream and are consumed by the parser.
- Expressive mode asks for one line per segment, so a spoken reply loses paragraph/markdown layout (D7).

## 6. Where performance comes from

The model decides. A new prompt layer `performance_output` (last in `PROMPT_LAYER_ORDER`) holds only an output-format instruction. It contains no emotion rules, mappings or defaults, and is byte-identical for every companion with the same capabilities. Everything that should shape delivery (temperament, mood, emotional associations, relationship, situation, behaviour block) is already in the prompt. Draft (≈600–900 chars, only supported fields listed):

> Your reply is spoken aloud. Put each stretch of speech you deliver differently on its own line. After a line you may add one line starting `@@ ` with a JSON object using only these fields: `<supported fields>`. Describe the delivery as you, in this character, would actually perform it right now; plain delivery needs no line; mixed feelings may list two emotions. Never mention these lines or the delivery in your words.

## 7. Capability gate and text-only fallback

The instruction is requested only when all hold: `performance_enabled` setting; the turn has a speech sink; its engine **declares** at least one of `emotion`, `tone`, `style`, `pace`. Otherwise no layer, no parser, no overhead, and the turn is byte-identical to today. If a declared control is later unmapped, the mapper drops it and the segment is spoken as plain text. A mapper error is logged and falls back to plain text; speech never fails because of metadata.

## 8. TTS compatibility (verified against this repo and installs)

| Class | Control | Finding |
|---|---|---|
| **A. Verified supported** | segment/sentence boundaries; pause after a segment | Chatbot-side: sentence chunking and PCM silence (`extra_pause`, bounded ≤0.4 s) exist. Engine-independent |
| **A.** | text, language, saved voice | The only inputs of the Qwen `speak` command |
| **B. Approximate, unverified here** | Qwen3-TTS emotion/tone/style via `instruct` | Exists in `generate_custom_voice` and `generate_voice_design` (installed source), natural language, ignored/disabled for the 0.6B variant; **not** in `generate_voice_clone`. Chatbot's conversational path is the clone path with library voices, so using it means a different model and fixed speakers (conflicts with unchanged voice selection) |
| **B.** | Fish Drama 3 emotion/tone/style | In-text bracket tags in the engine's `input`, as in the experiment script and Fish README (`[whisper]`, `[angry]`, free natural language). An adapter would render ≤1 bracket per segment into TTS input only (after `spoken_text`), never into chat. Effect through OpenRouter and cost are unverified; no adapter exists |
| **B.** | pace | No numeric rate field in any verified call; words in a Fish tag only, quality unknown; time-stretching PCM is not implemented |
| **C. Not supported** | Qwen Base clone: emotion, tone, style, pace, emphasis; SSML (no engine verified); AVTR expression | Not assumed. Emphasis is dropped everywhere unless an adapter proves a control |

Result today: no shipped engine declares a gate-opening control, so the feature is dark until an approved adapter does. TTS selection, voice selection and AVTR are unchanged.

## 9. Prompt and output-token overhead

- **Input:** +≤900 chars (~150–220 tokens), only when the gate is open; fewer with fewer supported fields. Same prefill-only effect as the Phase 4 block; reuse its gate (first-visible median +50 ms, p95 +100 ms).
- **Output (not free):** a trailer is ~20–30 tokens. Bounded to 6 per reply and 240 chars each. Expect ~40–100 extra output tokens on an expressive turn (typical spoken reply 40–120), i.e. roughly +50–100% of generated tokens when every segment is annotated, less when plain segments are left bare. Estimates, not measurements.
- **Latency:** first visible text unchanged (trailing metadata, no line-start buffering). First audio waits for the first segment's trailer, about 25 tokens ≈ 0.3–0.5 s at 50–80 tok/s, capped by the 300 ms grace. Total generation grows by metadata tokens ÷ decode rate; speech playback normally hides it.
- **Calls:** zero extra model, embedding or database calls. `ProviderConfig` unchanged. The 700-token background extraction budget is untouched: extraction receives dialogue only.

## 10. Minimal implementation interfaces

1. `chatbot/core/performance.py`: types above, `PerformanceCapabilities`, `performance_prompt_block(caps)`, `DialogueStreamParser`, `parse_dialogue`.
2. `prompt_layers.py` + `CompileContextStep`: add `PERFORMANCE_OUTPUT` layer when `ctx.metadata["performance_caps"]` is non-empty.
3. `api/streaming.py` and `CallProviderStep`: wrap provider text with the parser; yield dialogue deltas; set `provider_response` to the dialogue; store performance in metadata; a timer calls `expire`.
4. `avatar/coordinator.py`: queue item `SpeechSegment(text, delivery)`; `MediaRuntime.feed_text(turn_id, text, delivery=None)` and property `performance_capabilities` (default empty). With the gate closed, `publish` is unchanged.
5. `avatar/performance_mapping.py`: `SpeechDeliveryMapper` (`capabilities()`, `map(Performance) -> DeliveryControls | None`), default `NullMapper`; chunks never span two segments so a delivery applies to every sentence request of its segment; pause overrides the planner's `extra_pause`.
6. Inspector: `trace.prompt` shows the layer; `call_provider` step metadata gains `performance: {enabled, caps, segments, annotated, dropped:{reason:n}, released_by, first_segment_wait_ms}` and a ≤4 KB raw output sample (provider output, distinct from the dialogue).

## 11. Acceptance criteria

Deterministic, mocked provider, no TTS and no network:
1. Chunking invariance: one raw output split at every character boundary, inside `@@`, JSON and newlines gives identical segments.
2. SSE `token` texts concatenate to the dialogue; no `@@`, brace or key text ever appears; a non-`@` line start is forwarded on the first token.
3. `provider_response`, stored message, history, extraction input and `generated_response` equal the dialogue; raw text appears only in the bounded trace sample.
4. Every row of the §4 table keeps all dialogue, leaks nothing, and records its reason.
5. Gate closed: no layer, no parser, speech input byte-identical to baseline; `NullMapper` and mapper errors give plain speech.
6. Interrupt mid-segment and mid-trailer: persisted text follows the existing heard-text rule, no performance stored, no pending timer or task remains.
7. Standard, SSE and spoken produce equal `Dialogue` for the same raw output; the layer exists iff the gate is open.
8. Prompt block: lists only supported fields, is byte-identical across companions, ≤900 chars, contains no emotion-selection wording (golden test).
9. Exactly one provider call per turn, unchanged `ProviderConfig`; the 7th performance line is ignored.
10. Mapper stub: unsupported fields dropped; the bracket form appears only in the TTS request text, never in chat.

Owner-run later, real model and an adapter with a declared control: ≥30 spoken turns × 3 contrasting companions; malformed-line rate <2%; 0 visible leaks; metadata tokens median/p95; first-visible and first-audio deltas with the Phase 0 timings.

## 12. Decisions requiring owner approval

| # | Decision | Recommendation |
|---|---|---|
| D1 | Wire format: trailing `@@ {json}` lines after plain dialogue lines (vs whole-response JSON, or provider tool-calls, which are unverified for these models) | **Approve**: dialogue stays plain, fails open, no first-text delay |
| D2 | Capability gate: ask the model for performance only when the engine declares a control; today none does, so nothing ships visibly yet | **Approve**; build parser, fallback and Inspector first |
| D3 | Scope: `spoken` turns only at first (typed-with-avatar later) | **Approve** |
| D4 | Budgets: ≤6 trailers, ≤240 chars each, prompt ≤900 chars, grace 300 ms; first-audio cost ≈ first trailer | Approve as starting values; validate by measurement |
| D5 | Fish Drama 3 adapter (inline tags via OpenRouter) | Separate approved task; first verify tags are honoured and the per-byte cost |
| D6 | Qwen `instruct` needs CustomVoice and fixed speakers, not library clones | **Do not pursue**; keep Base clone plain text plus Chatbot-side pause |
| D7 | Spoken replies lose paragraph/markdown layout in expressive mode | **Accept** |
| D8 | Performance is not stored with messages (trace only) | **Approve**; revisit only for replay needs |
