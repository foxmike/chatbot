# Chatbot System Audit: Functional vs Scaffolded Components

This report details the audit of the chatbot codebase to classify 8 core systems as **WIRED** (functional end-to-end), **PARTIAL** (some parts functional), or **SCAFFOLDED** (exists but not connected).

---

## 1. Persona / System Prompt Pipeline
- **Status:** `PARTIAL`
- **Evidence:**
  - **In-memory cache bypass:** In [chat.py:L108-L112](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/chat.py#L108-L112), `_load_persona` is passed `session` loaded from the in-memory [sessions_store](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/sessions.py#L52), which is initialized to `{}` and never written to. As a result, the default fallback persona is used.
  - **Streaming context bypass:** In [streaming.py:L106](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/streaming.py#L106), `persona={}` is explicitly initialized as an empty dictionary.
  - **Assembler parameter gap:** In [turn.py:L2011-L2039](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py#L2011-L2039), `_assemble_persona_prompt` does not forward `character_profile` or `identity_state` to `assembler.assemble()`. Consequently, [CharacterProfile.to_prompt_fragment](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/character.py#L92) is never called in the chat execution flow, and no custom sliders, backstory, humor style, or communication styles are injected into the system prompt.
- **What's needed:** The chat and streaming endpoints must load the session's companion character profile and identity state from the database and pass them into the prompt compilation pipeline.

---

## 2. Memory System
- **Status:** `PARTIAL`
- **Evidence:**
  - **No embeddings generated during chat:** In [chat.py:L136-L155](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/chat.py#L136-L155) and [streaming.py:L432-L451](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/streaming.py#L432-L451), `Message` ORM objects are persisted to the database directly without generating or storing embedding vectors.
  - **Extraction step early exit:** In [turn.py:L605-L617](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py#L605-L617), the `ExtractMemoryStep` prints a warning and returns an empty list because database and storage parameters are missing from metadata.
  - **Context compiler unused:** The [ContextCompiler](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/context_compiler.py#L93) class is never imported or called during chat execution.
  - **Consolidation worker disconnected:** The [ConsolidationWorker](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/consolidation/worker.py#L45) is only set up in [consolidate_handler](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/perf/handlers.py#L102) which outputs a warning and skips execution, and `enqueue_consolidation` is never called.
  - **Redis unused:** Redis is only configured as a string in [config.py:L9](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/config.py#L9) and has no connection setup or operations in the codebase.
- **What's needed:** Message embeddings must be generated and stored upon message receipt, and the context compiler, background consolidation worker, and Redis cache must be integrated into the active turn pipeline.

---

## 3. Identity Drift and Mood
- **Status:** `SCAFFOLDED`
- **Evidence:**
  - **No pipeline updates:** In [turn.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py), there is no database reference or logic to read, calculate, or update the `IdentityStateModel.slow` or `IdentityStateModel.mood` values after a chat turn.
  - **Evolution logic disconnected:** The identity evolution logic in [evolution.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/evolution.py) is only imported to seed a new session in [sessions.py:L15](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/sessions.py#L15) and config management in [config.py:L27](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/config.py#L27), completely bypassing chat execution.
- **What's needed:** The pipeline must load the session's active identity state from the database, execute evolution drift calculations post-turn, and persist the updated slow and mood variables back to `IdentityStateModel`.

---

## 4. UI Tabs
- **Status:** `PARTIAL`
- **Evidence:**
  - **Memory tab (WIRED):** The component [MemoryPanel.tsx](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/components/MemoryPanel.tsx) calls real API endpoints (`getMemories`, `toggleMemoryPin`, etc.) which map to backend endpoints in [memory.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/memory.py) and query Postgres, but database entries remain empty due to disabled extraction.
  - **Timeline tab (WIRED):** The component [TimelinePanel.tsx](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/components/TimelinePanel.tsx) calls real API endpoints that query Postgres timeline tables, and the pipeline's [TimelineUpdateStep](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py#L642) dynamically detects and inserts milestones/emotional events.
  - **Voice tab (PARTIAL):** The component [VoiceControls.tsx](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/components/VoiceControls.tsx) calls API endpoints that initialize [TTSAdapter](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/voice/tts.py#L23) and [STTAdapter](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/voice/stt.py#L37) without API keys on the backend, leading to execution failures.
  - **Admin tab (WIRED):** The component [AdminDashboard.tsx](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/components/AdminDashboard.tsx) calls active endpoints in [admin.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/admin.py) querying database-backed tracing and metrics data.
- **What's needed:** Provide valid API credentials to the backend STT/TTS voice adapters and integrate memory extraction to populate memory tab tables.

---

## 5. Room-Chat Integration
- **Status:** `PARTIAL`
- **Evidence:**
  - **No UI message filtering:** Selecting a room via [RoomSwitcher.tsx](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/components/RoomSwitcher.tsx) updates the `activeRoomId` state in [App.tsx:L502](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/App.tsx#L502), but the chat messages shown in the chat panel are loaded using [messagesBySession](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/App.tsx#L281), which does not filter messages by room.
  - **No backend message filtering:** The backend endpoints in [sessions.py:L180](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/sessions.py#L180) also return all session messages without room filtering.
  - **No prompt injection:** While [RoomContextStep](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py#L699) loads the active scene, the room details and active scene descriptions are never formatted or injected into the compiled system prompt sent to the LLM.
- **What's needed:** Filter the displayed messages in both UI and backend endpoints by `roomId`, and modify the prompt compilation step to append scene descriptions and character overrides to the system prompt.

---

## 6. Sign Up / Registration
- **Status:** `WIRED`
- **Evidence:**
  - **Signup call:** Clicking the signup button in [AuthScreen.tsx:L111](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/components/AuthScreen.tsx#L111) calls `signup()` which hits [auth.py:L37-L77](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/auth.py#L37-L77).
  - **Database insertion:** The backend hashes the password, creates a new `User` row in the database, adds a `UserSession` token record, and saves changes via `db.commit()`.
- **What's needed:** Already fully functional.

---

## 7. Agency / Notifications
- **Status:** `WIRED`
- **Evidence:**
  - **Scheduler runtime:** The background proactive scheduler task is started at app startup in [app.py:L72](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/app.py#L72).
  - **Queue database backing:** It writes notifications to the DB-backed [NotificationQueue](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/agency.py#L25) which are exposed via endpoints in [agency.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/agency.py).
  - **UI integration:** The [NotificationFeed.tsx:L90](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/components/NotificationFeed.tsx#L90) component polls these notifications every 30 seconds and allows users to read/dismiss them, updating the database state.
- **What's needed:** Already fully functional.

---

## 8. Appearance System
- **Status:** `SCAFFOLDED`
- **Evidence:**
  - **Database bypass:** The visual API endpoints in [visual.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/visual.py) bypass the `"appearances"` table completely and use an in-memory `_appearance_manager` instance.
  - **Scaffolded provider:** The [DiffusionProvider](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/visual/generation.py#L36) class has no generation backend implemented and always raises a `GenerationError`.
  - **Missing UI code:** There are no calls or imports of any visual appearance/generation client features anywhere in the frontend `react_ui/src` codebase.
- **What's needed:** Connect the backend visual endpoints to the database `"appearances"` table, configure a real image generation API provider, and implement frontend UI controls to manage and display appearance/outfit data.
