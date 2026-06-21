# Chatbot Functionality Assessment

## 1. Executive Summary

Following the completion of the `CHAT_PROMPT_SYSTEM` plan, the application has transitioned from a basic multi-chat prototype into a modular, context-aware companion agent stack. Major advancements have been made in the prompt assembly pipeline, timescale-structured memory, companion linguistic fingerprints, and asynchronous background tasks.

Core conversational flows—including JWT-based authentication, user-scoped session creation, and durable message persistence in Postgres—are **Active in UI** and fully functional. The backend now integrates an in-process, asyncio-based [BackgroundWorker](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/perf/worker.py) running during the FastAPI lifespan to consume deferred memory extraction and consolidation tasks, solving the previous gap of unexecuted post-turn processing.

Vector memory retrieval has been activated: [PgvectorStore](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/pgvector_store.py) queries message embeddings dynamically with [RetrievalEngine](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/retrieval.py) fallback logic. Companion personality profiles now leverage a JSON-based linguistic fingerprint system, custom anti-repetition constraints, pacing rules, and an assistantism scrubber, which are fully editable in the React [CompanionEditor.tsx](file:///home/mike/dev/mike/chatbot/python/react_ui/src/components/CompanionEditor.tsx) component.

However, several components remain scaffold-only or bypassed: voice STT/TTS and visual image generation raise `NotImplementedError` or generation errors due to unconfigured external API providers. Prompt compression, attention anchoring, and narrative gravity weighting are defined in their respective modules but bypassed or not integrated into the default [TurnPipeline](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py). Crucially, a major security/isolation boundary gap persists: several secondary API routers (config, rooms, memory, agency, timeline, rituals, admin, streaming) are registered in [app.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/app.py) without authentication or user session-ownership checks.

---

## 2. Status Legend

* **Active in UI**: Fully functional and usable from the currently mounted React frontend app.
* **Active via API only**: The FastAPI routes are registered and functional, but there is no mounted frontend UI entry point to trigger them.
* **Backend wired, UI missing**: Backend persistence and domain logic are complete and wired, but frontend UI components are unmounted.
* **UI exists, not reachable**: React components or client-side pages exist in the frontend repository but are not mounted or routed in the app shell.
* **Runtime scaffold only**: Code exists as a service, adapter, helper, or step, but is either bypassed, mocked, or not integrated into the active execution path.
* **Model/schema only**: SQLAlchemy models or Alembic migrations exist, but there is no runtime, API router, or UI support.
* **Config missing**: The system requires external provider keys or local settings configurations that are absent or invalid.
* **Worker/runtime missing**: The system queues or schedules background work, but no active loop or process executes it.
* **Broken/stale**: Code is registered but fails at runtime due to route conflicts, database schema mismatches, or missing imports.
* **Missing**: No implementation exists in the codebase for the feature.

---

## 3. Feature Matrix

| Feature Area | Current Status | UI Reachable | API Reachable | Runtime Wired | Data Durable | User Scoped | Main Gap | Key Files |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
| **Durable Multi-Chat** | Active in UI | Yes | Yes | Yes | Yes | Yes | None; core flow is fully verified. | [sessions.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/sessions.py), [models.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/db/models.py), [App.tsx](file:///home/mike/dev/mike/chatbot/python/chatbot/react_ui/src/App.tsx) |
| **Auth & Multi-User** | Active in UI | Yes | Yes | Yes | Yes | Yes | Token expiry checking on reconnect is basic. | [auth/](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/auth/), [auth.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/auth.py) |
| **LLM Chat Pipeline** | Active in UI | Yes | Yes | Yes | Yes | Yes | Streaming is unauthenticated and bypassed in the UI. | [turn.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py), [chat.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/chat.py), [streaming.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/streaming.py) |
| **Memory System** | Active in UI | Yes | Yes | Yes | Yes | Yes | Vector retrieval depends on configured API keys; narrative gravity weighting is bypassed. | [retrieval.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/retrieval.py), [timescale.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/timescale.py), [pgvector_store.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/pgvector_store.py) |
| **Personality & Soul** | Active in UI | Yes | Yes | Yes | Yes | Yes | Config endpoints lack session ownership checks. | [persona_prompt.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/persona_prompt.py), [linguistic_fingerprint.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/linguistic_fingerprint.py), [CompanionEditor.tsx](file:///home/mike/dev/mike/chatbot/python/react_ui/src/components/CompanionEditor.tsx) |
| **Emotional Intelligence** | Active in UI | Yes | Yes | Yes | Yes | Yes | Bypassed on the fast path; run only on deep intent path. | [mood.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/mood.py), [user_state.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/user_state.py), [turn.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py#L401-L507) |
| **Mode & Room Contracts** | Backend wired, UI missing | No | Yes | Yes | Yes | No | Unauthenticated rooms API; UI switcher/scene controls unmounted. | [mode_contracts.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/mode_contracts.py), [rooms.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/rooms.py), [room.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/rooms/room.py) |
| **Continuity & Rituals** | Active via API only | No | Yes | Yes | Yes | No | Endpoints lack auth; timeline updates run only on deep path; UI Panel unmounted. | [timeline.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/continuity.py), [rituals.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/continuity/rituals.py) |
| **Proactive Agency** | Active via API only | No | Yes | Yes | Yes | No | Scheduler is DB-backed but agency endpoints are public; UI feed unmounted. | [agency.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/agency.py), [scheduler.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/agency/scheduler.py) |
| **Voice STT/TTS** | Broken/stale | No | Yes | No | Preferences only | Preferences only | Voice adapters raise `NotImplementedError`; frontend route mismatch proxy gap. | [tts.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/voice/tts.py), [stt.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/voice/stt.py), [prompt_adapter.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/voice/prompt_adapter.py) |
| **Visual Identity** | Runtime scaffold only | No | Yes | No | Model only | Model only | Generation service fails without configured API keys. | [generation.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/visual/generation.py), [visual.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/visual.py) |
| **Observability / Admin** | UI exists, not reachable | No | Yes | Yes | Yes | No | Dashboard is unmounted; admin routes are completely public. | [admin.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/admin.py), [prompt_telemetry.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/observability/prompt_telemetry.py) |
| **Asynchronous Worker** | Active in UI | Yes | Yes | Yes | Yes | Yes | Celery container in compose is unused; in-process asyncio lifespan worker handles tasks. | [worker.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/perf/worker.py), [handlers.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/perf/handlers.py) |

---

## 4. Detailed Feature Findings

### Durable Multi-Chat Conversations
* **Status**: **Active in UI**.
* **What is active and usable**: Authenticated users can create chat threads (`POST /sessions`), list existing sessions, fetch history for a session, and delete a session (`DELETE /sessions/{session_id}`). Deletions cascade to cleanly remove message records in Postgres.
* **What is not active**: Chat title renaming lacks a dedicated frontend input control (titles are auto-generated asynchronously via an LLM background task).
* **Data scoping**: Strongly scoped via JWT validation checks (`current_user.user_id == Session.user_id`).
* **Key Files**: [sessions.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/sessions.py), [models.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/db/models.py)

### Auth and Multi-User Companion App
* **Status**: **Active in UI**.
* **What is active and usable**: User registration, login, JWT token issuance, and token verification via `/auth/me` are wired into the React UI. Companions can be created, updated, and deleted using the Companion Editor.
* **What is not active**: The Vite frontend uses `/companions` API calls, and the companion router is correctly registered. Token revoking checks exist but lack regression coverage.
* **Data scoping**: Companion CRUD checks user ownership (`Companion.user_id == current_user.user_id`).
* **Key Files**: [auth/dependencies.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/auth/dependencies.py), [companions.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/companions.py), [CompanionSelector.tsx](file:///home/mike/dev/mike/chatbot/python/react_ui/src/components/CompanionSelector.tsx)

### LLM Chat Pipeline
* **Status**: **Active in UI**.
* **What is active and usable**: Synchronous chat turns run through `/chat/`. The pipeline performs intent classification to select the routing path. Simple intents (`greeting`, `casual`, `feedback`) follow the **Fast Path**, which compiles prompts, generates responses, and pushes post-turn tasks (`memory_extract`, `consolidate`) onto the background queue. Complex intents follow the **Deep Path**, running emotional and memory steps synchronously.
* **What is not active**: Streaming via `/chat/stream` is registered but remains unauthenticated, does not persist messages, and is bypassed in the mounted React UI.
* **Key Files**: [turn.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/turn.py), [steps.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/steps.py), [chat.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/chat.py)

### Memory System
* **Status**: **Active in UI**.
* **What is active and usable**:
  - **Working Memory**: Message history is loaded synchronously.
  - **Vector Retrieval**: [PgvectorStore](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/pgvector_store.py) performs similarity searches on message content embeddings.
  - **Timescale Structuring**: [timescale.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/timescale.py) classifies memories into four scales (`recent_conversational_state`, `stable_identity_fact`, `long_term_narrative_arc`, `active_goal`).
  - **Confidence Filter**: [confidence.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/confidence.py) drops low-scoring retrieved memories.
  - **Contradiction Resolution**: [ContradictionResolver](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/consolidation/contradiction.py) tags superseded memories during retrieval.
  - **Asynchronous Extraction & Consolidation**: Deployed in the lifespan asyncio worker.
* **What is not active**: Narrative gravity weighting ([narrative_gravity.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/narrative_gravity.py)) and session resume deltas ([session_delta.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/session_delta.py)) are bypassed and not imported in retrieval paths.
* **Key Files**: [retrieval.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/memory/retrieval.py), [worker.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/perf/worker.py)

### Personality / Soul System
* **Status**: **Active in UI**.
* **What is active and usable**: The [PersonaPromptAssembler](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/persona_prompt.py) builds system prompts on every turn. It formats the companion's backstory, personality sliders, and integrates:
  - **Linguistic Fingerprint**: Injects punctuation, slang, emoji, and vocabulary constraints from the companion's profile.
  - **Anti-Repetition**: Detects and prunes recently repeated word and phrase n-grams.
  - **Pacing Control**: Adjusts conversation cadence based on typing momentum metrics.
  - **Assistantism Scrubber**: Directs the LLM to strip customer-support verbal tics.
* **What is not active**: Relationship configs and character configurations can be edited via the `/config` API, but the endpoints do not verify session ownership.
* **Key Files**: [persona_prompt.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/persona_prompt.py), [linguistic_fingerprint.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/soul/linguistic_fingerprint.py)

### Mode and Room Contracts
* **Status**: **Backend wired, UI missing**.
* **What is active and usable**: Per-mode response contracts (`bedtime`, `advisor`, `deep_talk`, `roleplay`, `brainstorming`, `companion`) are fully wired into prompt layer assembly. Each contract defines token ratios, temperature modifiers, and active prompt layers. Room configurations map directly to mode contracts (e.g., `EMOTIONAL_SUPPORT` maps to `deep_talk`).
* **What is not active**: Room switcher and scene controls are unmounted in the frontend UI. The `/rooms` API endpoints accept arbitrary `user_id` query parameters and lack authentication.
* **Key Files**: [mode_contracts.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/core/mode_contracts.py), [rooms.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/rooms.py)

### Continuity and Rituals
* **Status**: **Active via API only**.
* **What is active and usable**: Endpoint routers for `/timeline`, `/narrative`, and `/rituals` are registered in the FastAPI app. Timeline updates and due rituals are evaluated in the deep turn pipeline path.
* **What is not active**: Endpoints lack auth. The React [TimelinePanel.tsx](file:///home/mike/dev/mike/chatbot/python/react_ui/src/components/TimelinePanel.tsx) is not mounted in the UI.
* **Key Files**: [continuity/](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/continuity/), [api/continuity.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/continuity.py)

### Proactive Agency
* **Status**: **Active via API only**.
* **What is active and usable**: DB-backed [Scheduler](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/agency/scheduler.py) runs a polling loop initialized during FastAPI app startup, checking schedules and queueing proactive check-ins and notifications.
* **What is not active**: Endpoints under `/agency` are public. The frontend notification feed component is unmounted.
* **Key Files**: [scheduler.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/agency/scheduler.py), [agency.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/agency.py)

### Voice Features
* **Status**: **Broken/stale**.
* **What is active and usable**: User voice preferences are persisted. The voice [prompt_adapter.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/voice/prompt_adapter.py) adapts prompt layers to guide the LLM to output speech-friendly text (no markdown, numbers written as words) when voice-mode is enabled.
* **What is not active**: TTS and STT adapters raise `NotImplementedError` due to missing provider integrations. Frontend calls to `/api/voice` suffer from a proxy path rewrite gap (Vite dev proxy strips `/api` causing backend route mismatches).
* **Key Files**: [prompt_adapter.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/voice/prompt_adapter.py), [voice.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/voice.py)

### Visual Identity
* **Status**: **Runtime scaffold only**.
* **What is active and usable**: API routes under `/visual` support uploading or generating companion avatars. Companion profiles reference these images.
* **What is not active**: Image generation services fail because no remote Diffusion API keys or endpoints are configured.
* **Key Files**: [generation.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/visual/generation.py), [visual.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/visual.py)

### Observability / Admin
* **Status**: **UI exists, not reachable**.
* **What is active and usable**: [PromptTelemetryCollector](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/observability/prompt_telemetry.py) records prompt configurations (template, model, mode, formatter) and links them to trace IDs. Turn execution step durations and errors are recorded.
* **What is not active**: The `AdminDashboard.tsx` component is unmounted. The `/admin` endpoints are completely public and lack authorization guards.
* **Key Files**: [admin.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/api/admin.py), [prompt_telemetry.py](file:///home/mike/dev/mike/chatbot/python/chatbot/chatbot/observability/prompt_telemetry.py)

---

## 5. Frontend Visibility Assessment

Normal authenticated users can interact with:
* **AuthScreen**: Sign up, log in, and establish JWT sessions.
* **SessionSidebar**: Create, select, switch, and delete chat threads.
* **MessageList**: View conversation histories, including companion responses.
* **ChatInput**: Send text messages.
* **CompanionEditor**: Modify companion names, free-form backstories, personality sliders, and JSON-based linguistic fingerprints.

The following frontend components exist in source but are **unmounted** and unreachable:
* `AdminDashboard.tsx`
* `CallMode.tsx` / `VoiceControls.tsx`
* `TimelinePanel.tsx`
* `RoomSwitcher.tsx` / `SceneControls.tsx`
* `NotificationFeed.tsx`
* `MemoryPanel.tsx`
* `RelationshipSettings.tsx`
* `CharacterCreator.tsx`

---

## 6. Runtime Wiring Assessment

### Active Standard Chat Paths
1. **Request Reception**: `POST /chat/` validates the user JWT.
2. **Context Setup**: Session history is loaded from Postgres. `_prepare_memory_workflow` generates missing message embeddings and initializes `RetrievalEngine`.
3. **Turn Execution**:
   - `ReceiveStep` strips user text.
   - `RoomContextStep` and `SceneAwareRetrievalStep` search the vector database (if room or vector keys exist).
   - **Intent Routing**: Intent classifier evaluates user input:
     - **Fast Path**: Casually routes, skips emotional updates, compiles context layers synchronously, queries the LLM provider, sends responses, and enqueues background consolidations.
     - **Deep Path**: Synchronously evaluates emotional states, updates moods, compiles context layers, calls the provider, and updates the database.
4. **Context Compilation**: Prompt layers are ordered: `safety_floor` → `platform_behavior` → `companion_identity` → `relationship_stage` → `room_scene` → `mood_state` → `memory` → `session_goals`. The formatter structures messages matching Anthropic, OpenAI, or Gemini formats, and the token budget allocator prunes layers.
5. **Persistence**: Message content is persisted durably to Postgres.
6. **lifespan Background Worker**: In-process worker consumes enqueued post-turn tasks.

### Bypassed Steps and Scaffolds
* **Attention Anchoring**: The system accepts attention anchors in prompt layers, but the turn step never fetches or passes anchors to prompt layers.
* **Session resume deltas**: Delta injection is defined in `session_delta.py` but never invoked.
* **Prompt Compression**: The `PromptCompressionStep` is defined but omitted from the default pipeline steps list.
* **Narrative Gravity Weighting**: Score adjustments are not integrated into retrieval engine routines.

---

## 7. Configuration and Provider Gaps

* **External API Keys**: `OPENROUTER_API_KEY` is required. The default embedding provider defaults to `openai/text-embedding-3-small` via OpenRouter. If the key is missing, vector memory retrieval gracefully falls back to empty lists.
* **Voice and Visual Adapters**: ElevenLabs and Stable Diffusion adapters are mock-only, raising `NotImplementedError` when called.
* **Proxy Configuration**: Vite proxy rewriting strips `/api/` prefixes, creating route matches with backend routers like `/memories` instead of `/api/memories`.

---

## 8. Data Durability and Isolation Assessment

### Survives Restart (Postgres)
* Users, Sessions, Messages, and Companions.
* Semantic Facts, Episodes, and Provenance columns.
* Identity states, relationship configurations, mood logs, and user state logs.
* Rooms, Scenes, Scheduler configurations, and Continuity events.

### Lost on Restart (In-Memory)
* `PersonaCache` records (in-process cache).
* In-flight `BackgroundQueue` tasks.
* In-memory structured telemetry traces.

### Security / Isolation Risks
* **No Authentication Guard**: Config settings, memory facts, timeline records, agency triggers, rooms, and admin logs can be accessed or modified by calling their endpoints directly with arbitrary session or user IDs.
* **Streaming Auth**: `/chat/stream` does not verify JWT credentials or check session ownership, allowing arbitrary context compilation inputs.

---

## 9. Cross-Cutting Gaps

1. **Security Vulnerability**: Critical lack of authentication and ownership checks on secondary routers (memory, config, rooms, timeline, rituals, narrative, admin, agency).
2. **Scaffold Bypasses**: Attention anchoring, delta resume injection, narrative gravity weighting, and prompt compression are scaffold-only.
3. **Mock Integrations**: STT/TTS and diffusion generation remain unimplemented mock adapters.
4. **Vite Proxy Prefix mismatch**: Mismatches between frontend `/api/memories` and backend `/memories` prefixes.
5. **Celery Scalability Discrepancy**: Compose runs a Celery worker container, but Python code does not configure Celery, relying entirely on the FastAPI asyncio lifespan worker.

---

## 10. Recommended Completion Strategy

### Phase 1 - API Security Hardening (Highest Priority)
* **Objective**: Guard all registered routers.
* **Actions**: Add `Depends(get_current_user)` to `/config`, `/memories`, `/rooms`, `/timeline`, `/rituals`, `/agency`, and `/admin`. Ensure session queries verify `Session.user_id == current_user.user_id`. Add admin role flags to protect observability endpoints.

### Phase 2 - Route Mismatch and UI Panel Integration
* **Objective**: Resolve proxy prefix issues and expose scaffolded controls.
* **Actions**: Align router prefixes (e.g. `/api/memories` in memory.py or remove api prefix in client.ts). Mount the unmounted settings, room switchers, memory panels, and admin dashboards in the authenticated app shell.

### Phase 3 - Real STT/TTS and Image Backends
* **Objective**: Wire voice and visual adapters to real providers.
* **Actions**: Implement ElevenLabs/Deepgram and local/remote diffusion providers in config and adapters, removing `NotImplementedError` fallbacks.

### Phase 4 - Pipeline Integrations
* **Objective**: Activate bypassed prompt and memory structures.
* **Actions**: Integrate prompt compression steps, narrative gravity weights, attention anchors, and session resume deltas into the turn execution steps.
