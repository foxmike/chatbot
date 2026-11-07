# COMPANION MEMORY, IDENTITY, AND CONTEXT ARCHITECTURE AUDIT

## 1. EXECUTIVE SUMMARY

The chatbot implements companion continuity through a hybrid layered pipeline combining fixed configuration, session-scoped dynamic state, and retrieval-augmented context. A companion's primary persona is defined in persistent database models (`companions`, `character_profiles`) containing backstories, communication styles, and fine-grained linguistic fingerprints (punctuation, emoji, sentence length, and vocabulary density). At runtime, conversation turns proceed through `TurnPipeline` (or `stream_chat` for streaming SSE and spoken avatar sessions). Recent conversational turns are preserved in PostgreSQL `messages` and loaded into a 20-message working memory window.

Long-term recall is driven by pgvector semantic retrieval over past turns and extracted semantic facts. Message embeddings are generated in an ordered batch via OpenRouter's `openai/text-embedding-3-small` (1536-d) preceding context compilation. While real-time emotional state (`mood`, `user_state`) and identity drift mutate session metadata, persistent companion persona definitions remain static across sessions unless explicitly updated via CRUD endpoints. Advanced features such as multi-timescale consolidation, contradiction decay, and memory promotion exist as modular background workers but remain detached or partially integrated in active production paths.

---

## 2. CAPABILITY INVENTORY

| Capability / Subsystem | Implementation Scope | Implementation Status | Active Path Tracing & Notes |
| :--- | :--- | :--- | :--- |
| **Companion Profile & Identity** | Global / Companion | **ACTIVE** | Stored in `companions` table; loaded in `chat.py` and `streaming.py`; assembled via `PersonaPromptAssembler`. |
| **Linguistic Fingerprint** | Companion | **ACTIVE** | DB fields (`punctuation_habits`, `sentence_length_profile`, `emoji_style`, `vocabulary_density`, `slang_profile`) injected into system prompt. |
| **Identity Drift (`evolution.py`)** | Session | **ACTIVE** | `calculate_drift` executes in `UpdateIdentityStep` and updates `identity_states` DB table; transient per session. |
| **Mood & Emotional State Engine** | Session | **ACTIVE** | `MoodEngine` updates mood state; logged to `bot_mood_logs` and `sessions.mood_state`. |
| **User State Inference** | Session | **ACTIVE** | `UserStateEngine` infers emotional distress/valence; logged to `user_state_logs` and `sessions.user_state`. |
| **Working Memory History** | Session | **ACTIVE** | `WorkingMemoryLoader` loads up to 20 recent messages (capped at ~4,000 estimated tokens) from DB `messages`. |
| **Recent Message Embedding Batching** | Session / Turn | **ACTIVE** | `_generate_recent_message_embeddings` fetches up to 20 un-embedded recent messages and embeds them in a single batch call. |
| **Episodic Memory Retrieval (pgvector)** | Session / Room / Global | **ACTIVE** | `RetrievalEngine` searches `Message` / `SemanticFact` vectors using cosine distance (`<->`); weighted ranking (72% vector, 18% recency, 10% relational). |
| **Rule-based Memory Extraction** | Session | **ACTIVE** | `MemoryExtractor` runs in `ExtractMemoryStep`, matching regex patterns (`_FACT_PATTERNS`) and inserting into `semantic_facts` & `episodes`. |
| **Context Compiler (`context_compiler.py`)** | Session / Turn | **UNUSED** | Standalone compiler class exists in `chatbot/memory/context_compiler.py` but is bypassed in favor of `_compile_context_text` in `turn.py`. |
| **Memory Consolidation & Pruning** | Session / User | **OPTIONAL / INCOMPLETE**| `ConsolidationWorker` and `contradiction.py` exist and are wired to `BackgroundQueue`, but queue processing is not triggered during standard streaming/spoken chat. |
| **Timeline & Milestone Tracking** | Session | **ACTIVE** | `TimelineUpdateStep` matches milestone keywords and commits to `timeline_entries` table. |
| **Room / Scene Context Memory** | Room / Scene | **ACTIVE** | `RoomContextStep` and `SceneMemoryGateStep` enforce room-type prompt overlays and scene isolation. |
| **Agency Rituals & Proactive Loop** | User | **OPTIONAL** | `Scheduler` background loop polls `agency_rituals` and `agency_timeline` every 300s; independent of chat turns. |

---

## 3. MEMORY SYSTEMS

| Name | Storage | Scope | Creation Event | Retention & TTL | Retrieval Trigger | Prompt Contribution | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Working Memory** | PostgreSQL `messages` table | Session (filtered by `room_id` if present) | User message submission and assistant generation completion | Indefinite DB persistence; retrieved window limited to 20 messages / 4000 tokens | Every turn at start of `RetrieveStep` | Injected as chronological dialog turns (`user: ...`, `assistant: ...`) | **ACTIVE** |
| **Episodic Turn Vectors** | PostgreSQL `messages.embedding` (pgvector 1536) | Session / Room / Companion | Generated before retrieval via `_generate_recent_message_embeddings` or post-turn `_generate_message_embeddings` | Indefinite DB retention | At turn execution via `RetrievalEngine.retrieve()` | Top-$K$ relevant turns injected under `Retrieved memories:` section | **ACTIVE** |
| **Semantic Facts** | PostgreSQL `semantic_facts` table (`embedding` column) | User (`user_id`) | Regex fact match during `ExtractMemoryStep` | Indefinite; no automatic expiration | Vector query via `PgvectorStore(SemanticFact)` | Formatted as bulleted fact lines under `Retrieved memories:` | **ACTIVE** |
| **Episode Summaries** | PostgreSQL `episodes` table | Session (`session_id`) | Extracted turn summary during `ExtractMemoryStep` | Indefinite | Queried via `retrieval.py` when vector store targets `episodes` | Injected as `- episode: <summary>` | **OPTIONAL / UNUSED** (default store targets `Message`) |
| **Timeline Entries** | PostgreSQL `timeline_entries` table | Session (`session_id`) | Regex milestone match in `TimelineUpdateStep` | Indefinite | Queried via continuity API; not automatically retrieved into core prompt | Available via room context or continuity inspection | **OPTIONAL** |
| **Identity & Mood Logs** | PostgreSQL `identity_states`, `bot_mood_logs`, `user_state_logs` | Session | End of turn (`UpdateIdentityStep`, `PersistEmotionalStateStep`) | Indefinite history logs | Loaded at session init / stream setup | Rendered as behavioral modifiers in companion system prompt | **ACTIVE** |
| **Narrative Threads** | PostgreSQL `narrative_threads` table | Session | Narrative tracking services | Indefinite | Read via continuity API | Not injected into standard chat turns | **UNUSED** |

---

## 4. EMBEDDING AND VECTOR RETRIEVAL

### 4.1 Embedding Provider and Model Selection
- **Provider**: `OpenRouterEmbeddingProvider` (subclassing `EmbeddingProvider` in `chatbot/memory/retrieval.py`).
- **Endpoint**: Configured via `embedding_base_url` (defaults to `openrouter_base_url` / `https://openrouter.ai/api/v1`) appending `/embeddings`.
- **Model Identifier**: Default `openai/text-embedding-3-small` (overridable via `embedding_model` setting).
- **Vector Dimensions**: 1536 dimensions. Validated dynamically against `provider.dimension`.
- **Fallback**: If `openrouter_api_key` is not configured, the engine instantiates `DisabledEmbeddingProvider`, logging warnings and skipping vector generation and search without failing the turn.

### 4.2 Storage and Indexing
- **Database Column**: `messages.embedding` and `semantic_facts.embedding` typed as `pgvector.sqlalchemy.vector.VECTOR(1536)`.
- **Index Type**: Default table migrations declare standard B-tree indexes on relational keys (`session_id`, `user_id`, `companion_id`). Vector distance relies on exact scan or pgvector HNSW/IVFFlat indexes if created in Postgres.
- **Distance Metric**: Cosine distance using the pgvector operator `<->`.

### 4.3 Ranking and Selection Logic
Retrieval executes in `chatbot/memory/retrieval.py:retrieve()`:
1. **Candidate Fetch**: Fetches top-$K$ candidates (default candidate pool $K = \max(20, 2 \times \text{limit})$) matching relational filters (`session_id`, optional date range, tags).
2. **Filtering & Deduplication**: Discards turns failing metadata constraints; deduplicates by turn ID and normalized message text.
3. **Contradiction Resolution & Timescaling**: Sorts candidates descending by timestamp. `ContradictionResolver` evaluates whether newer candidates supersede older ones.
4. **Composite Scoring Formula**:
   $$\text{Final Score} = 0.72 \times S_{\text{vector}} + 0.18 \times S_{\text{recency}} + 0.10 \times S_{\text{relational}}$$
   - $S_{\text{vector}} = \frac{1.0}{1.0 + \max(\text{distance}, 0.0)}$
   - $S_{\text{recency}} = \frac{t - t_{\min}}{t_{\max} - t_{\min}}$ (normalized across candidates)
   - $S_{\text{relational}} = \frac{\text{matching filter attributes}}{\text{checked filter attributes}}$
5. **Confidence Threshold**: Results are passed through `filter_low_confidence()`, pruning items scoring below confidence thresholds.

### 4.4 Recent Message Batching Optimization
- **Implementation**: Located in `chatbot/api/chat.py:_generate_recent_message_embeddings`.
- **Mechanism**: Prior to executing the turn pipeline, the API checks the last 20 messages in the session for `embedding IS NULL`. If un-embedded messages exist, all text payloads (`role: content`) are submitted to `OpenRouterEmbeddingProvider.embed([...])` in a **single HTTP batch request**, updating all message rows in a single DB flush.
- **Scope Limit**: Batching is executed in `chat.py` and `streaming.py` during `_prepare_memory_workflow`. The legacy post-turn step (`turn.py:_generate_message_embeddings`) remains sequential turn-by-turn fallback.

---

## 5. PERSONALITY AND IDENTITY

### 5.1 Static Definitions
A companion's core identity is configured in `chatbot/companions/companion.py` and stored in `companions` and `character_profiles`:
- **Identity & Persona**: `name`, `gender`, `persona`, `description`, `backstory`, `avatar_header`.
- **Communication Style**: `humor_style` (e.g., witty, sarcastic), `communication_style` (concise, expressive, verbose), `favorite_topics`.
- **Linguistic Fingerprint**: Fine-grained stylistic habits:
  - `punctuation_habits`: comma density, exclamation frequency, semicolon usage, ellipsis habit.
  - `sentence_length_profile`: target length, variation ratio, short sentence floor, long sentence ceiling.
  - `emoji_style`: frequency, placement (end, inline), allowed/preferred set.
  - `vocabulary_density`: lexical diversity and formality coefficient.
  - `slang_profile`: slang frequency and colloquial terms.

### 5.2 Dynamic & Runtime State
- **Mood Engine (`chatbot/soul/mood.py`)**: Tracks `energy`, `closeness`, and valence on a 0.0–1.0 scale. Updates dynamically per turn based on message sentiment and interaction duration. Mood decays back to baseline reset targets over configurable steps (`mood_decay_steps`).
- **Identity Evolution (`chatbot/soul/evolution.py`)**: Computes bounded trait drift via `calculate_drift()`. Tracks interaction counts and slowly drifts personality traits within strict percentage bounds ($\pm 15\%$) of the configured baseline.
- **User State Engine (`chatbot/soul/user_state.py`)**: Classifies the user's emotional state (`neutral`, `happy`, `distressed`, `frustrated`, `curious`) using keyword and regex heuristic scoring, guiding the companion's mode selection (`supportive`, `casual`, `deep`).

### 5.3 Evolution Reality Check
- **Fixed vs. Evolving**: Companion database records (`companions`) **never change** as a result of conversations. Only session-scoped identity rows (`identity_states`) and logs (`bot_mood_logs`) mutate.
- Across sessions, a companion resets to its configured database persona unless the frontend or client re-attaches to an existing active session with preserved `identity_states`.

---

## 6. CONTEXT AND PROMPT ASSEMBLY

When a turn executes, prompt construction follows a strict deterministic pipeline:

```
+-------------------------------------------------------------------+
| 1. System Prompt (PersonaPromptAssembler & compose_prompt_layers) |
|    - Identity, Tone, Traits, Backstory, Directives               |
|    - Linguistic Fingerprint Rules (Punctuation, Emoji, Slang)     |
|    - Relationship Config & Tone Modifiers                         |
|    - Room / Scene Context Prompt (if bound to a Room)             |
|    - Current Mood & Pacing Directives                             |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
| 2. Retrieved Long-Term Memory (RetrievalEngine)                   |
|    "Retrieved memories:"                                          |
|    - - message: <relevant past turn text>                         |
|    - - fact: <relevant user fact>                                 |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
| 3. Working Memory Conversation History                            |
|    "Conversation history:"                                        |
|    - user: <message n-k>                                          |
|    - assistant: <response n-k>                                    |
|    ...                                                            |
|    - user: <message n-1>                                          |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
| 4. Incoming User Message                                          |
|    user: <current turn prompt>                                    |
+-------------------------------------------------------------------+
```

### 6.1 Token Limits and Truncation
- `token_budget.py` defines a default context window of `8,192` tokens with a default response reserve of `1,024` tokens.
- `WorkingMemoryLoader` limits recent conversation turns to `max_turns = 20` and `max_tokens_estimate = 4,000`. Turns exceeding this floor are truncated oldest-first.
- Older messages beyond the 20-turn window remain in PostgreSQL but are never included in the direct prompt; they can only re-enter via vector retrieval.

### 6.2 Inspection vs. Provider Payload
- **Admin Trace Inspection (`/admin/traces/{trace_id}`)**: Inspects `TraceRecord.prompt`, which stores `_render_trace_prompt(user_message, history)`.
- **Payload Difference**: The trace shows the flattened text representation (`system: ... \n\n user: ... \n\n assistant: ...`). However, the actual HTTP payload sent to OpenAI/Ollama/OpenRouter providers in `OpenAICompatibleProvider._build_payload` separates messages into structured JSON dictionaries: `[{"role": "system", ...}, {"role": "user", ...}]` with model-specific fields (e.g., `reasoning_effort` for Ollama/o-series models).

---

## 7. MEMORY LIFECYCLE

```
[ User Sends Message ]
         |
         +--> [ DB Write: messages (user) ]
         |
         +--> [ Embedding Batch: OpenRouter Embeddings API ]
         |         |
         |         +--> [ pgvector UPDATE: messages.embedding ]
         |
         +--> [ RetrievalEngine Query: Cosine Distance Search ]
         |         |
         |         +--> [ Top-K Filtered, Ranked, Contradiction-Checked ]
         |
         +--> [ Prompt Assembly: System Layers + Memories + Working Turns ]
         |
         +--> [ LLM Completion: Streaming or Non-Streaming ]
         |
         +--> [ DB Write: messages (assistant) ]
         |
         +--> [ ExtractMemoryStep (Regex Pattern Scanner) ]
         |         |
         |         +--> [ INSERT: semantic_facts (if user fact matched) ]
         |         +--> [ INSERT: episodes (turn summary) ]
         |
         +--> [ TimelineUpdateStep (Milestone Scanner) ]
         |         |
         |         +--> [ INSERT: timeline_entries (if keyword matched) ]
         |
         +--> [ UpdateIdentityStep (Evolution & Mood Calculation) ]
                   |
                   +--> [ UPDATE: identity_states, INSERT: bot_mood_logs ]
```

- **Conflict Resolution**: Contradictory facts or memories are evaluated by `ContradictionResolver`. If a contradiction is detected, older conflicting candidates are marked as superseded and excluded from retrieval.
- **Decay & Deletion**: Memory records in `messages`, `semantic_facts`, and `episodes` have **no time-to-live (TTL) and are never automatically deleted**. Retention in PostgreSQL is indefinite.

---

## 8. CONVERSATION PATH CONSISTENCY

| Feature / Step | Standard Text Chat (`/chat/`) | SSE Streaming Chat (`/chat/stream`) | Spoken / Avatar (`/avatar/ws`) |
| :--- | :--- | :--- | :--- |
| **Endpoint / Protocol** | HTTP POST (`ChatResponse`) | HTTP POST SSE (`text/event-stream`) | WebSocket (`_spoken_turn` wrapper) |
| **Pipeline Runner** | `TurnPipeline.execute_turn` | Generator loop in `_stream_turn` | Calls `stream_chat` directly |
| **Companion Profile** | Loaded via `CompanionManager` | Loaded via `CompanionManager` | Loaded via `CompanionManager` |
| **Working Memory** | DB message history | DB message history | DB message history |
| **Embedding Batching** | `_generate_recent_message_embeddings` | `_generate_recent_message_embeddings` | `_generate_recent_message_embeddings` |
| **Retrieval Engine** | `_build_chat_retrieval_engine` | Handled via `_prepare_memory_workflow` | Identical to SSE Streaming |
| **Identity Drift** | Runs in `UpdateIdentityStep` | Skipped during stream; post-turn task | Handled via stream task |
| **Message Persistence** | Post-pipeline DB commit | `_persist_messages` on complete | `_persist_messages` with cancellation shield |

The three paths share identical companion identity, working memory history, retrieval logic, and embedding batch optimizations.

---

## 9. LIMITATIONS AND DISCONNECTED FEATURES

1. **Disconnected ContextCompiler**: `chatbot/memory/context_compiler.py` defines an elaborate budgeting and token-packing system (`ContextCompiler`, `ContextSource`), but it is not called anywhere in `turn.py`, `chat.py`, or `streaming.py`. Prompt assembly uses simplified concatenation in `_compile_context_text`.
2. **Consolidation Worker Inactivity in Main Flows**: While `ConsolidationWorker` is implemented and registered in `app.py` for `BackgroundWorker`, standard streaming chat (`/chat/stream`) and spoken avatar turns do not enqueue deferred consolidation tasks. Only synchronous non-room fast-path calls with a background queue enqueue them.
3. **Regex-Only Memory Extraction**: Memory extraction (`MemoryExtractor`) relies entirely on static regular expressions (`_FACT_PATTERNS`, e.g., `"i am"`, `"my favorite"`). It does not use LLM-based memory distillation; subtle or implicit user preferences are not captured.
4. **Indefinite Storage Growth**: There are no retention limits, archiving policies, or database purging routines for `messages`, `semantic_facts`, or `bot_mood_logs`.
5. **No Cross-Session Personality Persistence**: Drifts calculated by `evolution.py` are persisted to `identity_states` scoped to a single `session_id`. Starting a new session reverts the companion entirely to its static baseline configuration.

---

## 10. CONFIGURATION REFERENCE

| Parameter | Environment Variable | Default Value | Active Source File |
| :--- | :--- | :--- | :--- |
| `openrouter_api_key` | `OPENROUTER_API_KEY` | `""` (Disabled) | `chatbot/config.py:46` |
| `openrouter_base_url`| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | `chatbot/config.py:47` |
| `embedding_model` | `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | `chatbot/config.py:65` |
| `embedding_base_url` | `EMBEDDING_BASE_URL` | None (falls back to OpenRouter) | `chatbot/config.py:66` |
| `default_model` | `DEFAULT_MODEL` | `meta-llama/llama-3.3-70b-instruct` | `chatbot/config.py:48` |
| `default_provider` | `DEFAULT_PROVIDER` | `openrouter` | `chatbot/config.py:53` |
| `database_url` | `DATABASE_URL` | `postgresql+asyncpg://...` | `chatbot/config.py:27` |
| `context_window` | `CONTEXT_WINDOW` | `8192` | `chatbot/core/token_budget.py:16` |
| `max_turns` | (Hardcoded) | `20` | `chatbot/memory/working_memory.py:108` |
| `max_tokens_estimate`| (Hardcoded) | `4000` | `chatbot/memory/working_memory.py:109` |
| `drift_rate` | `DRIFT_RATE` | `0.02` (2%) | `chatbot/soul/evolution.py:17` |
| `drift_bound_ratio` | `DRIFT_BOUND_RATIO` | `0.15` (15%) | `chatbot/soul/evolution.py:19` |

---

## 11. SOURCE REFERENCES

- **API & Endpoints**:
  - `chatbot/api/chat.py`: Lines 82–245 (`chat`), Lines 350–415 (`_generate_recent_message_embeddings`), Lines 419–465 (`_build_chat_retrieval_engine`).
  - `chatbot/api/streaming.py`: Lines 125–385 (`_stream_turn`), Lines 500–560 (`_stream_provider_call`), Lines 890–945 (`_load_stream_companion_profile`).
  - `chatbot/avatar/router.py`: Lines 85–125 (`_spoken_turn`).
- **Core Pipeline & Prompt Assembly**:
  - `chatbot/core/turn.py`: Lines 140–205 (Step definitions), Lines 510–600 (`CompileContextStep`), Lines 1137–1250 (`TurnPipeline`), Lines 3164–3200 (`_compile_context_text`), Lines 3364–3410 (`_formatted_provider_inputs`).
  - `chatbot/soul/persona_prompt.py`: Lines 91–230 (`PersonaPromptAssembler.assemble`).
  - `chatbot/soul/linguistic_fingerprint.py`: Lines 291–345 (`format_linguistic_fingerprint`).
- **Memory & Embeddings**:
  - `chatbot/memory/retrieval.py`: Lines 90–240 (`RetrievalEngine.retrieve`), Lines 380–440 (`_ranking_score`), Lines 715–775 (`OpenRouterEmbeddingProvider`).
  - `chatbot/memory/embedding.py`: Lines 15–75 (`generate_embedding`).
  - `chatbot/memory/extraction.py`: Lines 88–145 (`MemoryExtractor`), Lines 220–260 (`_extract_entries`), Lines 420–445 (`_looks_like_user_fact`).
  - `chatbot/memory/working_memory.py`: Lines 99–155 (`WorkingMemoryLoader.load`).
  - `chatbot/memory/pgvector_store.py`: Lines 45–95 (`PgvectorStore.search`).
- **Database Schema**:
  - `chatbot/db/models.py`: Lines 50–120 (`Companion`), Lines 150–175 (`IdentityStateModel`), Lines 200–235 (`Message`), Lines 240–265 (`SemanticFact`).
