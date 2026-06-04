# Chatbot Functionality Assessment

## 1. Executive Summary

The application is currently usable as an authenticated, durable multi-chat web app. A normal React user can sign up or log in, create chats, switch between chats, send messages, reload persisted messages, and delete chats. Those flows use FastAPI auth, user-scoped `sessions` and `messages` tables, and the main React app shell.

Much more functionality exists in backend packages, database models, API routers, and React components, but most of it is not visible in the main UI. Memory panels, relationship settings, character creator, continuity timeline, rooms/scenes, voice controls, agency notifications, companion selection, and admin dashboard components exist in `react_ui/src/components/`, but `react_ui/src/App.tsx` only mounts auth, the session sidebar, message list, and chat input.

The standard chat runtime is intentionally fast-path only when `chatbot.perf.background.BackgroundQueue` is available. In the current code, `chatbot/api/chat.py` creates an in-process `BackgroundQueue`, so `TurnPipeline.run()` executes only `receive`, `retrieve`, `compile_context`, `call_provider`, and `respond`, then enqueues deferred memory extraction and consolidation. No app lifespan task, worker process, or Docker Compose worker consumes that queue, so deferred memory work does not actually run.

Provider/config gaps block several scaffolded systems. OpenRouter chat is registered, but `OPENROUTER_API_KEY` is optional and missing keys cause provider calls to fail. Embedding, TTS, STT, image generation, image understanding, scheduler, and task-class model routing are not configured in `chatbot/config.py`. Voice and visual adapters validate model identifiers but do not call real providers.

The first development push should focus on feature visibility and route correctness for already-built components, then background worker/runtime execution, then memory activation. User-scoping must be tightened before exposing API-only features because several routers accept arbitrary `user_id` or `session_id` values without the existing auth dependency.

## 2. Status Legend

Use these exact labels:

* Active in UI: usable from the currently mounted React app.
* Active via API only: API is registered and callable, but there is no reachable UI entry point.
* Backend wired, UI missing: backend and persistence exist, and frontend code may exist, but normal users cannot reach it.
* UI exists, not reachable: React component/client code exists but is not mounted or routed in the app shell.
* Runtime scaffold only: code exists as a service, adapter, step, or queue, but does not run end to end.
* Model/schema only: SQLAlchemy models or type/schema definitions exist without active runtime/API/UI support.
* Config missing: implementation depends on provider or settings values that are absent or incomplete.
* Worker/runtime missing: queued, scheduled, or background work has no active worker/runtime.
* Broken/stale: code exists but is mismatched with current routes, migrations, app registration, or runtime behavior.
* Missing: no implementation was found for the expected capability.
* Unknown / needs manual verification: evidence exists but behavior was not proven from source inspection alone.

## 3. Feature Matrix

| Feature Area | Current Status | UI Reachable | API Reachable | Runtime Wired | Data Durable | User Scoped | Main Gap | Key Files |
| ------------ | -------------- | -----------: | ------------: | ------------: | -----------: | ----------: | -------- | --------- |
| Durable multi-chat conversations | Active in UI | Yes | Yes | Yes | Yes | Yes | Chat title updates and broader E2E verification remain; current core flow is implemented. | `chatbot/api/sessions.py`, `chatbot/api/chat.py`, `chatbot/db/models.py`, `react_ui/src/App.tsx`, `react_ui/src/components/SessionSidebar.tsx` |
| Auth and multi-user companion app | Broken/stale | Partial | Partial | Partial | Partial | Partial | Auth works; companion backend manager exists but no registered companion router in app. | `chatbot/api/auth.py`, `chatbot/auth/`, `chatbot/companions/companion.py`, `react_ui/src/components/AuthScreen.tsx`, `react_ui/src/components/CompanionSelector.tsx` |
| LLM chat pipeline | Active in UI | Yes | Yes | Partial | Messages yes | Yes for `/chat/` | Fast path runs; streaming is API-only and unauthenticated; deferred work has no worker. | `chatbot/api/chat.py`, `chatbot/api/streaming.py`, `chatbot/core/turn.py`, `chatbot/providers/` |
| Memory system | Broken/stale | No | Partial | Partial | Partial | No | Memory router path conflicts with Vite rewrite; no auth scoping; retrieval engine and embedding provider are not wired. | `chatbot/api/memory.py`, `chatbot/memory/`, `chatbot/consolidation/`, `react_ui/src/components/MemoryPanel.tsx` |
| Personality / soul system | Backend wired, UI missing | No | Partial | Partial | Partial | By session only | Prompt assembly runs in chat, but relationship/character/identity UI is not mounted and config endpoints lack auth ownership checks. | `chatbot/soul/`, `chatbot/api/config.py`, `react_ui/src/components/CharacterCreator.tsx`, `react_ui/src/components/RelationshipSettings.tsx` |
| Emotional intelligence | Runtime scaffold only | No | No dedicated API | Mostly bypassed | Partial | By session only | EI steps exist but are skipped in fast-path chat when background queue is enabled. | `chatbot/core/turn.py`, `chatbot/soul/mood.py`, `chatbot/soul/user_state.py`, `migrations/versions/0002_emotional_state.py` |
| Relationship and character configuration | UI exists, not reachable | No | Yes | Partial | Yes | No | API and components exist, but UI is not mounted and endpoints do not verify session ownership. | `chatbot/api/config.py`, `chatbot/soul/relationship.py`, `chatbot/soul/character.py`, `react_ui/src/components/RelationshipSettings.tsx`, `react_ui/src/components/CharacterCreator.tsx` |
| Continuity features | Broken/stale | No | No registered API | Mostly bypassed | Partial/uncertain | No | Frontend calls `/api/timeline`, `/api/narrative`, `/api/rituals`, but no matching routers are registered. | `chatbot/continuity/`, `react_ui/src/components/TimelinePanel.tsx`, `react_ui/src/api/client.ts` |
| Proactive agency | Runtime scaffold only | No | Yes | No | No | No | In-memory agency services and no scheduler/worker runtime; DB models are not used by API. | `chatbot/api/agency.py`, `chatbot/agency/`, `migrations/versions/0006_agency.py`, `react_ui/src/components/NotificationFeed.tsx` |
| Rooms / scenes / roleplay | Backend wired, UI missing | No | Yes | Partial | Yes | No | Room APIs are registered, but not auth scoped or mounted in UI; chat requests do not carry `room_id`. | `chatbot/api/rooms.py`, `chatbot/rooms/`, `migrations/versions/0007_rooms_scenes.py`, `react_ui/src/components/RoomSwitcher.tsx`, `react_ui/src/components/SceneControls.tsx` |
| Voice features | Broken/stale | No | Partial | No | Model only | Preferences only | `/api/voice` prefix conflicts with Vite rewrite; TTS/STT provider calls raise `NotImplementedError`; `voice_preferences` migration not found. | `chatbot/api/voice.py`, `chatbot/voice/`, `react_ui/src/components/VoiceControls.tsx`, `react_ui/src/components/CallMode.tsx` |
| Visual identity | Runtime scaffold only | No | No | No | Model only | Partial/uncertain | Models and adapters exist, but no registered API, no migration found for visual tables, and no real provider backend configured. | `chatbot/visual/`, `chatbot/db/models.py` |
| Observability / admin | UI exists, not reachable | No | Yes | Partial | No | No | Admin dashboard is unmounted; collectors are in-memory despite DB observability tables; endpoints are not admin-gated. | `chatbot/api/admin.py`, `chatbot/observability/`, `migrations/versions/0009_observability.py`, `react_ui/src/components/AdminDashboard.tsx` |
| Performance / scaling | Worker/runtime missing | No | No | Partial | No | N/A | Persona cache is in-process; background queue is in-process and unconsumed; Redis is configured but not used by these paths. | `chatbot/perf/background.py`, `chatbot/perf/cache.py`, `docker-compose.yml`, `chatbot/api/chat.py` |

## 4. Detailed Feature Findings

### Durable multi-chat conversations

* Status: Active in UI.
* What is built: `chatbot/api/sessions.py` provides authenticated `POST /sessions/`, `GET /sessions/`, `GET /sessions/{session_id}`, `GET /sessions/{session_id}/messages`, and `DELETE /sessions/{session_id}`. `chatbot/api/chat.py` provides authenticated `POST /chat/` and persists both user and assistant `Message` rows. `react_ui/src/App.tsx` keeps `messagesBySession` keyed by session id and uses `SessionSidebar` for create/select/delete.
* What is active and usable: A signed-in user can create durable chats, switch active chats, load messages for the active chat, send messages to the active chat, and delete a chat.
* What is not active or not usable: No title rename endpoint or visible rename control was found. Chat streaming is not used by the active send path.
* UI status: Active in `react_ui/src/App.tsx` and `react_ui/src/components/SessionSidebar.tsx`.
* API status: Registered in `chatbot/api/app.py` through `sessions_router` and `chat_router`.
* Runtime/chat-pipeline status: Standard send path calls `sendMessage()` -> `/chat/` -> `TurnPipeline`.
* Data/storage status: Durable in Postgres through `sessions` and `messages`. `messages` are ordered by `created_at` and `id` in `list_session_messages()`.
* User isolation status: Strong for session list, get, messages, delete, and chat send; these endpoints use `get_current_user` and filter by `Session.user_id`.
* Missing configuration/provider/worker support: OpenRouter config is needed for real assistant responses. Without it, provider calls can fail or return provider-error text.
* Files inspected: `chatbot/api/sessions.py`, `chatbot/api/chat.py`, `chatbot/db/models.py`, `migrations/versions/0001_core_schema.py`, `migrations/versions/0008_auth_companions.py`, `react_ui/src/App.tsx`, `react_ui/src/api/client.ts`, `react_ui/src/components/SessionSidebar.tsx`.
* Highest-value next step: Add focused regression coverage for multi-chat persistence/isolation and decide whether chat rename should be first-class UI.

### Auth and multi-user companion app

* Status: Broken/stale.
* What is built: `chatbot/api/auth.py` exposes signup, login, logout, and `/auth/me`. `chatbot/auth/dependencies.py` validates bearer tokens. `chatbot/config.py` has a stable dev `jwt_secret_key`. `chatbot/companions/companion.py` has a `CompanionManager` with create/list/get/update/delete behavior and user checks.
* What is active and usable: Signup/login/me are used by the React app. User-scoped session and message persistence is active.
* What is not active or not usable: No companion router is registered in `chatbot/api/app.py`. `react_ui/src/api/client.ts` contains `/companions` functions and `CompanionSelector.tsx` exists, but those routes are not present in the real FastAPI app. `tests/test_auth_companions.py` defines a local test-only companions router, which does not prove production registration.
* UI status: Auth screen is active. Companion selector exists but is not mounted.
* API status: Auth API is registered. Companion API is missing from the app registration.
* Runtime/chat-pipeline status: `ChatRequest` and session creation accept `companion_id`, and `CompanionManager.get()` is used to validate a companion if supplied. Since companion CRUD is not exposed, normal users cannot create/select companions through the real app.
* Data/storage status: `users`, `user_sessions`, and `companions` tables are created by `0008_auth_companions.py`. Sessions and messages can store `companion_id`.
* User isolation status: Active auth/session routes are user-scoped. Companion manager methods check user ownership, but the missing router prevents production use.
* Missing configuration/provider/worker support: None for auth. Companion UI/API registration is the blocker.
* Files inspected: `chatbot/api/auth.py`, `chatbot/auth/dependencies.py`, `chatbot/auth/security.py`, `chatbot/companions/companion.py`, `chatbot/api/app.py`, `tests/test_auth_companions.py`, `react_ui/src/components/AuthScreen.tsx`, `react_ui/src/components/CompanionSelector.tsx`.
* Highest-value next step: Add/register a production companion router using the existing `CompanionManager`, then mount companion selection in the authenticated app shell.

### LLM chat pipeline

* Status: Active in UI.
* What is built: `chatbot/core/turn.py` defines a full turn pipeline with receive, room context, retrieval, emotional inference, mode selection, persona/context compilation, provider call, mood update, emotional-state persistence, memory extraction, scene memory gating, timeline update, and respond steps. `chatbot/providers/bootstrap.py` registers OpenRouter, and `chatbot/providers/openai_compat.py` implements OpenAI-compatible chat completion calls.
* What is active and usable: The non-streaming `/chat/` endpoint is active from the React app. It loads DB history, compiles context, calls a provider when configured, persists messages, and returns a response.
* What is not active or not usable: `/chat/stream` is registered but not used by `App.tsx`. `MessageList.tsx` has streaming support only when a `streamRequest` prop is supplied; `App.tsx` does not supply it. Streaming endpoint lacks auth/session ownership checks and does not persist messages. OpenRouter adapter does not implement a stream method, so token streaming falls back to chunking a full provider response if used.
* UI status: Non-streaming chat is active. Typing indicator is active through `isLoading`. Streaming UI scaffold exists but is not reached by current app state.
* API status: `/chat/` and `/chat/stream` are registered.
* Runtime/chat-pipeline status: In standard chat, `chatbot/api/chat.py` creates `BackgroundQueue`, so `TurnPipeline.run()` executes only `FAST_PATH_STEPS`: `receive`, `retrieve`, `compile_context`, `call_provider`, and `respond`. It enqueues `extract_memory` and `consolidate` tasks but does not execute the rest of the full pipeline.
* Data/storage status: `/chat/` persists messages. `/chat/stream` does not.
* User isolation status: `/chat/` is user-scoped. `/chat/stream` is not authenticated.
* Missing configuration/provider/worker support: `OPENROUTER_API_KEY` is required for real OpenRouter calls. No model-routing settings are loaded from config. No worker consumes deferred tasks.
* Files inspected: `chatbot/api/chat.py`, `chatbot/api/streaming.py`, `chatbot/core/turn.py`, `chatbot/providers/bootstrap.py`, `chatbot/providers/openai_compat.py`, `chatbot/providers/router.py`, `react_ui/src/App.tsx`, `react_ui/src/components/MessageList.tsx`.
* Highest-value next step: Decide whether streaming should be production-supported; if yes, add auth/session ownership, persistence, and mount a streaming send flow.

### Memory system

* Status: Broken/stale.
* What is built: Memory models exist for `episodes`, `semantic_facts`, and `entities`. `chatbot/memory/` includes working memory, extraction, retrieval, pgvector store, search, export, provenance, and controls. `chatbot/consolidation/` includes summarizer, contradiction, decay, and worker modules. `chatbot/api/memory.py` exposes list, search, export, provenance, pin, delete, edit, privacy, do-not-remember, remember-forever, and correct endpoints.
* What is active and usable: DB message history is loaded into the standard prompt path through `chatbot/api/chat.py`. Memory search/control API code is registered in FastAPI.
* What is not active or not usable: The memory router uses `APIRouter(prefix="/api/memories")`, but Vite strips `/api` before proxying. Frontend calls such as `/api/memories` become backend `/memories`, which does not match registered `/api/memories`. The memory router has no auth dependency and allows arbitrary `user_id` filters. The standard chat path does not inject a `retrieval_engine` into app state, and deferred extraction/consolidation is queued but not consumed.
* UI status: `MemoryPanel.tsx` and `HistorySearch.tsx` exist but are not mounted.
* API status: Registered, but path mismatched for frontend proxy use and not user-scoped.
* Runtime/chat-pipeline status: Working-memory history is active. Vector retrieval, memory extraction, consolidation, pruning, contradiction resolution, and provenance-writing are not active in the normal fast path.
* Data/storage status: Core memory tables and migrations exist for semantic facts/provenance. Memory durability depends on extraction or manual API writes, neither of which is active from the UI.
* User isolation status: Not safe for UI exposure as-is; router lacks `get_current_user` and ownership checks.
* Missing configuration/provider/worker support: Embedding provider/model config is missing. `retrieval_engine` app-state wiring is missing. Background worker is missing.
* Files inspected: `chatbot/api/memory.py`, `chatbot/memory/`, `chatbot/consolidation/`, `chatbot/core/turn.py`, `chatbot/api/app.py`, `react_ui/src/components/MemoryPanel.tsx`, `react_ui/src/components/HistorySearch.tsx`, `react_ui/src/api/client.ts`.
* Highest-value next step: Fix route prefix/auth ownership first, then wire embeddings, pgvector retrieval, extraction, consolidation, and visible memory controls.

### Personality / soul system

* Status: Backend wired, UI missing.
* What is built: `chatbot/soul/` contains personality, persona prompt assembly, mood, user state, modes, relationship, character, identity, and evolution modules. `chatbot/api/config.py` exposes relationship, character, and identity endpoints. DB models exist for relationship configs, character profiles, identity states, mood logs, and user state logs.
* What is active and usable: `CompileContextStep` uses `PersonaPromptAssembler` when available. `PersonaCache` is created in `chatbot/api/chat.py` and wired into the pipeline. Relationship/character/identity API endpoints are registered under `/config`.
* What is not active or not usable: The character and relationship React components are not mounted. Identity evolution is not visible. `chatbot/api/config.py` does not authenticate or verify that the session belongs to the current user.
* UI status: `RelationshipSettings.tsx` and `CharacterCreator.tsx` exist but are not reachable.
* API status: `/config/relationship/{session_id}`, `/config/character/{session_id}`, and `/config/identity/{session_id}` are registered.
* Runtime/chat-pipeline status: Prompt assembly is active in standard chat. Controlled identity evolution is API-only and not automatically proven in the standard fast path.
* Data/storage status: Durable relationship, character, and identity tables have migrations.
* User isolation status: Session-scoped, but not user-auth scoped at the config API layer.
* Missing configuration/provider/worker support: No external provider required for base prompt assembly. Cache invalidation after config changes is not visible in `chatbot/api/config.py`.
* Files inspected: `chatbot/soul/persona_prompt.py`, `chatbot/soul/personality.py`, `chatbot/soul/relationship.py`, `chatbot/soul/character.py`, `chatbot/soul/evolution.py`, `chatbot/api/config.py`, `migrations/versions/95005655ed55_add_relationship_and_character_tables.py`, `react_ui/src/components/RelationshipSettings.tsx`, `react_ui/src/components/CharacterCreator.tsx`.
* Highest-value next step: Add auth ownership checks to config endpoints, then expose relationship and character panels for the active session.

### Emotional intelligence

* Status: Runtime scaffold only.
* What is built: Mood, user-state inference, conversation mode selection, mood update, and emotional-state persistence steps exist in `chatbot/core/turn.py` and `chatbot/soul/`. Migration `0002_emotional_state.py` adds `sessions.mood_state`, `sessions.user_state`, `sessions.conversation_mode`, `bot_mood_logs`, and `user_state_logs`.
* What is active and usable: The code can run if the full pipeline runs without a background queue.
* What is not active or not usable: Because `chatbot/api/chat.py` creates a `BackgroundQueue`, standard chat uses `FAST_PATH_STEPS` and skips `infer_user_state`, `select_conversation_mode`, `update_mood`, and `persist_emotional_state`. There is no visible mood/status UI.
* UI status: No active UI. Voice cadence can accept mood/mode values, but voice is not active.
* API status: No dedicated emotional-state API was found. State is present through config/identity and session model fields, not a complete API surface.
* Runtime/chat-pipeline status: Mostly bypassed in current standard chat path.
* Data/storage status: Durable columns/log tables exist.
* User isolation status: Data is session-related, but no dedicated user-scoped emotional API was found.
* Missing configuration/provider/worker support: Needs runtime decision: run EI on fast path, move it to real deferred processing, or add a worker that executes it.
* Files inspected: `chatbot/core/turn.py`, `chatbot/soul/mood.py`, `chatbot/soul/user_state.py`, `chatbot/soul/modes.py`, `migrations/versions/0002_emotional_state.py`, `chatbot/db/models.py`.
* Highest-value next step: Define which EI steps must affect the immediate response, then wire those steps into the actual chat runtime.

### Relationship and character configuration

* Status: UI exists, not reachable.
* What is built: Relationship settings API and character profile API exist in `chatbot/api/config.py`. React components `RelationshipSettings.tsx` and `CharacterCreator.tsx` load and update these APIs. Models and migrations exist.
* What is active and usable: API-only if a caller knows the session id. No normal UI entry point.
* What is not active or not usable: Not mounted in `App.tsx`. The endpoints do not require auth and do not verify session ownership.
* UI status: Components exist but are unmounted.
* API status: Registered under `/config`.
* Runtime/chat-pipeline status: Persona prompt assembly can use relationship/character data if the assembler loads it; this should be manually verified after UI/API exposure.
* Data/storage status: Durable in `relationship_configs`, `character_profiles`, and `identity_states`.
* User isolation status: Not safe for exposure until config endpoints verify the active user owns the session.
* Missing configuration/provider/worker support: No provider config needed. Cache invalidation should be added if persona cache can serve stale profile data.
* Files inspected: `chatbot/api/config.py`, `chatbot/soul/relationship.py`, `chatbot/soul/character.py`, `chatbot/soul/persona_prompt.py`, `react_ui/src/components/RelationshipSettings.tsx`, `react_ui/src/components/CharacterCreator.tsx`, `react_ui/src/api/client.ts`.
* Highest-value next step: Add session ownership checks and mount the components in an active-session settings panel.

### Continuity features

* Status: Broken/stale.
* What is built: `chatbot/continuity/` contains timeline, narrative, and rituals modules. `TimelinePanel.tsx` exists and the API client has `fetchTimeline`, active projects, pending promises, narrative threads, callbacks, rituals, create ritual, and deactivate ritual methods.
* What is active and usable: No registered continuity API router was found. `TimelineUpdateStep` exists in the pipeline but is skipped in the standard fast path with background queue enabled.
* What is not active or not usable: Frontend client calls `/api/timeline`, `/api/narrative`, and `/api/rituals`, but `chatbot/api/app.py` does not register matching routers. `chatbot/continuity/timeline.py` and `chatbot/continuity/rituals.py` define SQLAlchemy models with table names `relationship_timeline_entries` and `companion_rituals`; the inspected migrations create `timeline_entries`, `narrative_threads`, and `rituals` instead. That naming mismatch is a migration/schema risk.
* UI status: `TimelinePanel.tsx` exists but is not mounted.
* API status: Missing registered routes for frontend continuity methods.
* Runtime/chat-pipeline status: Timeline update step is not in `FAST_PATH_STEPS` or `DEFERRED_STEPS`, so it does not execute in standard chat when a background queue exists.
* Data/storage status: Migrations exist for continuity tables, but module-level models appear to target different table names than `chatbot/db/models.py`.
* User isolation status: Not established because no production API router was found.
* Missing configuration/provider/worker support: Needs route registration, schema reconciliation, and runtime wiring.
* Files inspected: `chatbot/continuity/timeline.py`, `chatbot/continuity/narrative.py`, `chatbot/continuity/rituals.py`, `chatbot/core/turn.py`, `migrations/versions/0005_continuity.py`, `react_ui/src/components/TimelinePanel.tsx`, `react_ui/src/api/client.ts`, `chatbot/api/app.py`.
* Highest-value next step: Reconcile continuity table/model names and create user-scoped routers that match existing frontend client paths.

### Proactive agency

* Status: Runtime scaffold only.
* What is built: `chatbot/agency/` includes scheduler, notifications, initiative, and check-in generation. `chatbot/api/agency.py` exposes initiative, notification, trigger check-in, and schedule endpoints. Migration `0006_agency.py` creates agency tables.
* What is active and usable: API endpoints are registered under `/agency`.
* What is not active or not usable: API uses module-level in-memory `Scheduler`, `NotificationQueue`, `ProjectInitiative`, and `CheckInGenerator`. It does not use the agency DB tables in the inspected code. No background scheduler/worker is launched by app lifespan or Docker Compose. UI components are not mounted.
* UI status: `NotificationFeed.tsx` and `InitiativeSettings.tsx` exist but are not reachable.
* API status: Registered.
* Runtime/chat-pipeline status: Proactive check-ins and schedules require a runtime loop, which was not found.
* Data/storage status: API state is in-memory; migration tables exist but are not used by the API.
* User isolation status: Unsafe for exposure: routes accept arbitrary `user_id` and do not use `get_current_user`.
* Missing configuration/provider/worker support: Scheduler runtime/worker missing. Durable persistence missing in API.
* Files inspected: `chatbot/api/agency.py`, `chatbot/agency/scheduler.py`, `chatbot/agency/notifications.py`, `chatbot/agency/initiative.py`, `chatbot/agency/checkins.py`, `migrations/versions/0006_agency.py`, `react_ui/src/components/NotificationFeed.tsx`, `react_ui/src/components/InitiativeSettings.tsx`.
* Highest-value next step: Convert agency endpoints to authenticated, durable state and add a real scheduler/worker before exposing UI.

### Rooms / scenes / roleplay

* Status: Backend wired, UI missing.
* What is built: `chatbot/api/rooms.py` registers room and scene CRUD, active scene, pause/resume/end, scene persona prompt, and memory classification endpoints. `chatbot/rooms/` has room, scene, and roleplay domain modules. Migration `0007_rooms_scenes.py` creates `rooms` and `scenes`.
* What is active and usable: API is registered and can persist room/scene records.
* What is not active or not usable: Room and scene components are not mounted. `ChatRequest` does not include `room_id`; standard chat metadata therefore does not carry room context from the frontend. Endpoints accept arbitrary `user_id` and do not authenticate.
* UI status: `RoomSwitcher.tsx` and `SceneControls.tsx` exist but are not reachable.
* API status: Registered under `/rooms`.
* Runtime/chat-pipeline status: `RoomContextStep`, `SceneAwareRetrievalStep`, and `SceneMemoryGateStep` exist, but room context only runs if `ctx.metadata["room_id"]` exists. The standard chat request does not set it.
* Data/storage status: Durable in `rooms` and `scenes`.
* User isolation status: Not safe as-is; room APIs are user-id-param based and not auth scoped.
* Missing configuration/provider/worker support: Needs UI selection state, chat request wiring for `room_id`, and auth checks.
* Files inspected: `chatbot/api/rooms.py`, `chatbot/rooms/room.py`, `chatbot/rooms/scene.py`, `chatbot/rooms/roleplay.py`, `chatbot/core/turn.py`, `migrations/versions/0007_rooms_scenes.py`, `react_ui/src/components/RoomSwitcher.tsx`, `react_ui/src/components/SceneControls.tsx`.
* Highest-value next step: Add auth ownership to rooms/scenes, mount controls, and pass active `room_id` through chat.

### Voice features

* Status: Broken/stale.
* What is built: Voice API exposes synthesize, transcribe, get preferences, and update preferences. TTS/STT adapters validate known provider/model identifiers and cadence mapping exists. React has `VoiceControls.tsx`, `CallMode.tsx`, and `useAudioRecorder.ts`.
* What is active and usable: No voice UI is active. Voice preferences endpoints are registered but route path and migration issues block confidence.
* What is not active or not usable: `voice_router` uses `prefix="/api/voice"`, while Vite strips `/api`, so frontend `/api/voice/preferences` becomes backend `/voice/preferences`, not `/api/voice/preferences`. `TTSAdapter._call_provider()` and `STTAdapter._call_provider()` raise `NotImplementedError`. No migration creating `voice_preferences` was found in `migrations/versions/`.
* UI status: Components/hooks exist but are not mounted.
* API status: Registered, but frontend path mismatch exists. Synthesize/transcribe do not require auth; preferences do.
* Runtime/chat-pipeline status: Call mode can call `sendMessage`, but it is not mounted and voice transcription/synthesis providers are not implemented.
* Data/storage status: `VoicePreferences` model exists. No migration was found for `voice_preferences`, so database availability is unverified and likely broken after migration-only setup.
* User isolation status: Preferences use `get_current_user`. Synthesize/transcribe are not user-scoped.
* Missing configuration/provider/worker support: TTS/STT provider config and API implementation are missing.
* Files inspected: `chatbot/api/voice.py`, `chatbot/voice/tts.py`, `chatbot/voice/stt.py`, `chatbot/voice/cadence.py`, `chatbot/db/models.py`, `migrations/versions/`, `react_ui/src/components/VoiceControls.tsx`, `react_ui/src/components/CallMode.tsx`, `react_ui/src/hooks/useAudioRecorder.ts`.
* Highest-value next step: Decide provider support, add settings/migration if needed, fix route prefix, and keep UI hidden until synthesize/transcribe work.

### Visual identity

* Status: Runtime scaffold only.
* What is built: `chatbot/db/models.py` defines `Appearance`, `OutfitRecord`, and `ImageMemory`. `chatbot/visual/appearance.py` has appearance/outfit managers. `chatbot/visual/generation.py` and `chatbot/visual/understanding.py` define provider-neutral generation/understanding services.
* What is active and usable: No active UI or API was found.
* What is not active or not usable: No visual router is registered in `chatbot/api/app.py`. `DiffusionProvider.generate()` raises `GenerationError` because no backend is configured. Image understanding requires an injected callable. No migration creating `appearances`, `outfit_records`, or `image_memories` was found.
* UI status: No mounted visual identity UI was found.
* API status: Missing.
* Runtime/chat-pipeline status: Not wired into chat.
* Data/storage status: Model/schema only; migration coverage was not found.
* User isolation status: Models include `user_id` on some visual records, but end-to-end isolation cannot be assessed without API/runtime.
* Missing configuration/provider/worker support: Image generation/understanding provider config, router, migrations, and UI are missing.
* Files inspected: `chatbot/visual/appearance.py`, `chatbot/visual/generation.py`, `chatbot/visual/understanding.py`, `chatbot/visual/base.py`, `chatbot/db/models.py`, `migrations/versions/`, `chatbot/api/app.py`.
* Highest-value next step: Treat this as inactive until migrations, provider config, and a user-scoped API are added.

### Observability / admin

* Status: UI exists, not reachable.
* What is built: `chatbot/api/admin.py` exposes traces, trace detail, metrics, logs, usage, and admin health. `chatbot/observability/` includes trace, metrics, and structured log collectors. `AdminDashboard.tsx` exists. Migration `0009_observability.py` creates observability tables.
* What is active and usable: Admin API is registered. The turn pipeline records trace steps and metrics to in-memory collectors.
* What is not active or not usable: `AdminDashboard.tsx` is not mounted. `TraceCollector`, `MetricsCollector`, and `StructuredLogger` store data in process memory; inspected admin endpoints do not read/write `TraceRecord` or `UsageRecord` SQLAlchemy tables. Endpoints are not authenticated or admin-gated.
* UI status: Component exists but is not reachable.
* API status: Registered under `/admin`.
* Runtime/chat-pipeline status: Trace/metrics collection is called in the pipeline, but only for steps that actually run.
* Data/storage status: In-memory at runtime; DB observability tables exist but are unused by inspected collectors.
* User isolation status: Not safe for exposure; admin endpoints are public in source.
* Missing configuration/provider/worker support: Needs auth/admin policy, optional persistence, and UI mounting.
* Files inspected: `chatbot/api/admin.py`, `chatbot/observability/tracing.py`, `chatbot/observability/metrics.py`, `chatbot/observability/logs.py`, `migrations/versions/0009_observability.py`, `react_ui/src/components/AdminDashboard.tsx`.
* Highest-value next step: Add admin authorization and decide whether observability should persist to DB before mounting dashboard.

### Performance / scaling

* Status: Worker/runtime missing.
* What is built: `chatbot/perf/cache.py` implements an in-process persona cache. `chatbot/perf/background.py` implements an in-process priority queue and `run_background_worker()`. `chatbot/config.py` loads `redis_url`, and Docker Compose runs Redis.
* What is active and usable: Persona cache is created by `chatbot/api/chat.py` and passed into `build_default_pipeline()`. Background queue is created and receives deferred tasks.
* What is not active or not usable: No worker is started in FastAPI lifespan. No Docker Compose worker service exists. Redis is not used by the inspected background queue or persona cache.
* UI status: No performance UI.
* API status: No performance/admin controls beyond in-memory admin metrics.
* Runtime/chat-pipeline status: Fast path is active. Deferred work is queued but unprocessed.
* Data/storage status: Cache and queue are in process and lost on restart.
* User isolation status: Persona cache keys by `(user_id, companion_id)` when those values are present.
* Missing configuration/provider/worker support: Need worker lifecycle, task handlers, durable queue decision, Redis use decision, and operational controls.
* Files inspected: `chatbot/perf/background.py`, `chatbot/perf/cache.py`, `chatbot/api/chat.py`, `chatbot/core/turn.py`, `docker-compose.yml`, `chatbot/config.py`.
* Highest-value next step: Implement a real Background Worker runtime before relying on deferred memory, continuity, or proactive work.

## 5. Frontend Visibility Assessment

A normal user can currently see and use:

* `AuthScreen`: signup, login, existing-token `/auth/me` check.
* `SessionSidebar`: list chats, create chat, select chat, delete chat.
* `MessageList`: persisted message display and non-streaming typing indicator.
* `ChatInput`: send message to the active session.

Existing React components that are not reachable from the main UI:

* `AdminDashboard.tsx`
* `CallMode.tsx`
* `CharacterCreator.tsx`
* `CompanionSelector.tsx`
* `HistorySearch.tsx`
* `InitiativeSettings.tsx`
* `MemoryPanel.tsx`
* `NotificationFeed.tsx`
* `RelationshipSettings.tsx`
* `RoomSwitcher.tsx`
* `SceneControls.tsx`
* `TimelinePanel.tsx`
* `VoiceControls.tsx`
* `useAudioRecorder.ts`

API client methods with no visible UI entry point include relationship config, character profile, identity state, memory controls/search/export/provenance, timeline/narrative/rituals, initiative, notifications, voice preferences/synthesize/transcribe, rooms/scenes, companions, streaming chat, and admin dashboard methods.

Feature areas needing navigation/settings/sidebar/panel integration:

* Companion selection and companion management.
* Active-session settings for relationship, character, identity, memory, timeline, room/scene, and voice.
* Notification feed and initiative settings.
* Admin dashboard behind an admin-only entry point.
* Streaming/call mode if it is intended to be user-facing.

Important route/API mismatches visible from frontend source:

* Vite strips `/api` before proxying. Frontend memory and voice calls use `/api/memories` and `/api/voice`, while backend routers are also registered with `/api/memories` and `/api/voice`. Through the dev proxy, these requests become `/memories` and `/voice`, which do not match the backend route prefixes.
* Frontend continuity calls use `/api/timeline`, `/api/narrative`, and `/api/rituals`, but no matching routers were found in `chatbot/api/app.py`.
* Frontend companion calls use `/companions`, but no companion router is registered in `chatbot/api/app.py`.

## 6. Runtime Wiring Assessment

Active standard chat steps:

* React `handleSend()` sends to `sendMessage(message, activeSessionId)`.
* `POST /chat/` validates the JWT and verifies the session belongs to the current user.
* DB message history is loaded for the session.
* `TurnPipeline` receives metadata with provider registry, model/provider defaults if present, persona cache, session history, user id, and optional companion id.
* With a background queue present, the pipeline executes `receive`, `retrieve`, `compile_context`, `call_provider`, and `respond`.
* `/chat/` persists the user message and assistant response to Postgres.

Inactive or bypassed steps in standard chat:

* `room_context` is not in `FAST_PATH_STEPS`.
* `infer_user_state`, `select_conversation_mode`, `update_mood`, and `persist_emotional_state` are not in `FAST_PATH_STEPS`.
* `scene_memory_gate` and `timeline_update` are not in `FAST_PATH_STEPS` or `DEFERRED_STEPS`.
* `extract_memory` and `consolidate` are deferred but not consumed.

Deferred queued work:

* `TurnPipeline.enqueue_deferred()` enqueues `memory_extract` and `consolidate` tasks using `BackgroundQueue`.
* No active Background Worker was found to call `run_background_worker()` or handle those task payloads.

Missing worker problems:

* Memory extraction, consolidation, decay/pruning, contradiction handling, proactive scheduling, notification delivery, and timeline updates cannot be assumed to run after turns.
* In-process queue contents are lost on process restart.

Missing retrieval_engine problems:

* `SceneAwareRetrievalStep` can call a retrieval engine with room context, but no `retrieval_engine` is configured in FastAPI lifespan.
* Without a retrieval engine, standard retrieval is effectively recent history/working memory, not pgvector semantic retrieval.

Missing embedding provider problems:

* `chatbot/config.py` has no embedding provider/model settings.
* OpenRouter chat provider setup does not register an embedding provider.
* pgvector-backed semantic retrieval cannot be activated deterministically without embedding configuration.

Provider/model routing behavior:

* `ProviderRouter` and task-class routing code exist.
* `build_default_registry()` registers OpenRouter only.
* No runtime settings were found to enable task-class routing or configure per-task models.

Streaming behavior:

* `/chat/stream` emits SSE typing, step, token, response, error, and done events.
* It is not authenticated, does not verify session ownership, starts with empty history/persona, and does not persist messages.
* `MessageList.tsx` can display streaming if `streamRequest` is passed, but `App.tsx` currently uses non-streaming `sendMessage()`.

## 7. Configuration and Provider Gaps

Embeddings/vector memory:

* Missing embedding provider settings in `chatbot/config.py`.
* Missing retrieval engine initialization in `chatbot/api/app.py`.
* Missing verified pgvector embedding write/read path in the active chat runtime.

Memory extraction/consolidation:

* Memory extractor and consolidation modules exist.
* Deferred queue exists, but no worker consumes it.
* No handler wiring was found for `memory_extract` and `consolidate` tasks.

Voice/STT/TTS:

* TTS and STT adapters validate identifiers but provider calls raise `NotImplementedError`.
* No TTS/STT API keys, base URLs, or provider settings exist in `chatbot/config.py`.
* Voice API path is mismatched with the Vite proxy.
* `voice_preferences` migration was not found.

Image generation/understanding:

* Diffusion provider raises `GenerationError` because no backend is configured.
* Image understanding requires an injected callable; none is wired.
* No visual API router is registered.
* Migrations for `appearances`, `outfit_records`, and `image_memories` were not found.

Proactive scheduling:

* Scheduler is in memory and no runtime loop was found.
* No durable scheduler state is used by the agency API despite agency tables existing.

Admin/model routing:

* Admin endpoints are public in source.
* Observability collectors are in memory despite migrations for observability tables.
* Task-class model routing exists but is not configured.

Other provider-dependent systems:

* OpenRouter requires `OPENROUTER_API_KEY`.
* `chatbot/providers/modal_ephemeral.py` exists, but no default registry wiring was found for it.

## 8. Data Durability and Isolation Assessment

Data that survives restart:

* Users, sessions, messages, companion rows, relationship configs, character profiles, identity states, emotional-state session columns/logs, semantic facts, episodes, provenance columns, rooms, scenes, agency tables, continuity tables from migrations, and observability tables from migrations can exist in Postgres.
* In the active UI, sessions and messages definitely use Postgres and survive restart.

Data that does not survive restart in the inspected runtime:

* `sessions_store` legacy in-memory cache in `chatbot/api/sessions.py`.
* `PersonaCache`.
* `BackgroundQueue` and any queued deferred tasks.
* Agency scheduler, initiative settings, and notifications as exposed by `chatbot/api/agency.py`.
* Observability traces, metrics, and structured logs collected by `chatbot/observability/`.

Per-user and per-session isolation:

* Authenticated session and chat endpoints verify `Session.user_id == current_user.user_id`.
* Message persistence stores `user_id` and optional `companion_id`.
* Voice preferences use current user, but voice synthesize/transcribe are unauthenticated.
* Relationship/character/identity config endpoints are session-id based and do not verify current user ownership.
* Memory endpoints are unauthenticated and accept arbitrary `user_id` filters.
* Rooms endpoints are unauthenticated and accept arbitrary `user_id`.
* Agency endpoints are unauthenticated and accept arbitrary `user_id`.
* Admin endpoints are unauthenticated.
* Streaming chat is unauthenticated and not durable.

Isolation risks or unverified areas:

* Do not expose API-only components broadly until auth dependencies and ownership checks are added.
* Continuity modules and migrations appear to disagree on table names, so durability and isolation are unverified there.
* Visual models exist but migrations and API are missing; isolation is model-only.

## 9. Cross-Cutting Gaps

* Missing Background Worker runtime for deferred memory extraction, consolidation, and any queued post-turn work.
* Missing embedding provider and retrieval engine wiring for semantic/vector memory.
* Frontend feature discoverability is low because most components are unmounted.
* Route/API mismatches exist for memory, voice, continuity, and companions.
* Several routers are not auth scoped: memory, config, agency, rooms, admin, streaming.
* Observability, agency, persona cache, and background queue are in-memory despite durable tables or Redis availability.
* Voice and visual providers are unconfigured or intentionally unimplemented.
* Continuity table/model naming appears stale or inconsistent.
* `docker-compose.yml` includes API, UI, DB, and Redis, but no worker.
* `chatbot/config.py` loads DB, Redis, JWT, OpenRouter, default model, and provider settings only; no settings for embeddings, voice, visual, scheduler, admin auth, or routing policies.
* Tests may contain local routers or fixtures that do not match production app registration; `tests/test_auth_companions.py` is an example for companions.

## 10. Recommended Completion Strategy

### Phase 1 - Feature Visibility and App Shell

* Purpose: Expose reachable navigation/settings/panels for already-built APIs and components.
* Why it comes in this order: The codebase already contains many components; making them visible reveals the real integration gaps without changing core architecture.
* Main dependencies: Route correctness and auth ownership checks for any exposed API.
* Candidate work items: Add an authenticated app shell with settings/panels; mount companion selector, relationship settings, character creator, memory panel, timeline panel, rooms/scenes controls, voice controls, notification feed, and admin dashboard only where safe; fix frontend/backend route mismatches.
* Acceptance criteria: Every mounted component reaches a registered backend route, handles auth, and shows data for the active user/session only.
* Risks: Exposing unauthenticated API-only surfaces too early can leak or mutate cross-user data.

### Phase 2 - Background Worker and Deferred Runtime

* Purpose: Make queued/deferred work actually execute.
* Why it comes in this order: Memory, continuity, proactive agency, and performance claims depend on deferred execution.
* Main dependencies: Task handler definitions, lifecycle management, shutdown behavior, and Docker Compose worker support.
* Candidate work items: Start a worker in app lifespan or add a worker service; define handlers for `memory_extract` and `consolidate`; decide whether Redis should back queues; add operational health checks.
* Acceptance criteria: Deferred tasks created by chat are consumed, retries behave deterministically, and restart behavior is documented.
* Risks: In-process workers can duplicate work under multi-process deployment; durable queues need careful idempotency.

### Phase 3 - Memory Activation and Configuration

* Purpose: Wire embeddings, retrieval, extraction, consolidation, memory controls, and memory UI.
* Why it comes in this order: Memory needs the worker from Phase 2 and route/auth fixes from Phase 1.
* Main dependencies: Embedding provider config, pgvector availability, retrieval engine initialization, user/session ownership.
* Candidate work items: Add embedding settings; initialize retrieval engine; run extraction/consolidation through worker; fix memory route prefix; add auth scoping; mount `MemoryPanel` and `HistorySearch`; verify provenance and delete/correct controls.
* Acceptance criteria: A user message can produce durable memory, later turns retrieve it for the same user/session scope, and another user cannot read or mutate it.
* Risks: Incorrect scoping can leak semantic facts; bad embeddings config can silently degrade retrieval.

### Phase 4 - Persona, Identity, Relationship, and Character Usability

* Purpose: Make personality/identity/relationship systems configurable and visible.
* Why it comes in this order: Persona prompt assembly is already part of the standard chat path, so visible configuration can immediately improve the active product.
* Main dependencies: User-owned session checks and persona cache invalidation.
* Candidate work items: Secure config endpoints; mount relationship and character panels; expose identity state/evolution controls carefully; invalidate persona cache on updates.
* Acceptance criteria: Updates affect only owned sessions and are reflected in subsequent prompts.
* Risks: Stale cache can make UI changes appear ignored; identity evolution can drift without clear controls.

### Phase 5 - Continuity, Rituals, Agency, and Notifications

* Purpose: Activate timeline, narrative threads, rituals, proactive check-ins, notifications, and initiative controls.
* Why it comes in this order: These systems require worker/runtime execution and user-scoped UI patterns.
* Main dependencies: Schema reconciliation for continuity, durable agency storage, scheduler runtime.
* Candidate work items: Register continuity routers; reconcile table/model names; wire timeline update into actual runtime; convert agency API to durable/user-scoped state; mount timeline, initiative, and notification panels.
* Acceptance criteria: Timeline and notifications survive restart and are isolated by user/session.
* Risks: Current stale route/schema mismatch could require migrations; proactive features can annoy users without strong controls.

### Phase 6 - Rooms, Scenes, and Roleplay Isolation

* Purpose: Make room/scene controls and room-scoped memory usable.
* Why it comes in this order: Rooms need active memory/retrieval and app shell support.
* Main dependencies: Auth-scoped rooms API, active room state in frontend, chat request metadata.
* Candidate work items: Add ownership checks; mount room/scene controls; pass `room_id` through chat; ensure room-scoped memory retrieval and scene memory gating run in the actual pipeline.
* Acceptance criteria: Room-scoped chats use the selected scene context and do not mix scene-only memory into durable global memory.
* Risks: Without clear active-room state, messages can be sent to the wrong context.

### Phase 7 - Voice and Call Mode

* Purpose: Make voice operational or explicitly configurable.
* Why it comes in this order: Voice depends on provider choices and active chat reliability.
* Main dependencies: TTS/STT provider config, voice preferences migration, route prefix fix.
* Candidate work items: Add provider settings; implement TTS/STT calls; add migration if needed; secure voice endpoints; mount voice controls/call mode.
* Acceptance criteria: Transcription and synthesis work with configured providers, preferences persist per user, and disabled voice is respected.
* Risks: Provider latency/cost and browser audio permission errors require clear UI handling.

### Phase 8 - Visual Identity and Image Features

* Purpose: Make avatar, appearance, image messages, and visual provider behavior usable.
* Why it comes in this order: Visual features currently have the least API/runtime support and need provider decisions.
* Main dependencies: Visual migrations, provider config, user-scoped API, frontend display controls.
* Candidate work items: Add visual tables migration if needed; register image generation/understanding routes; wire appearance/outfit continuity; mount avatar/image UI.
* Acceptance criteria: Visual records persist and are isolated by user/session/companion, and provider failure is explicit.
* Risks: Binary image storage and provider costs need operational limits.

### Phase 9 - Observability, Admin, Routing, Streaming, and Performance

* Purpose: Make admin dashboard, traces, usage, model routing, streaming, cache, and performance controls usable.
* Why it comes in this order: Admin and performance work should observe the now-active systems.
* Main dependencies: Admin auth policy, persistence decision for observability, streaming auth/persistence, model-routing settings.
* Candidate work items: Gate admin routes; persist or intentionally scope observability data; configure task-class routing; secure and mount streaming chat; decide Redis use for cache/queues.
* Acceptance criteria: Admin data reflects real requests, is not public, and streaming behaves consistently with non-streaming chat.
* Risks: Public admin endpoints are a security issue; streaming without persistence can diverge from durable chat history.

### Phase 10 - Full Integration Verification and Documentation

* Purpose: End-to-end verification, multi-user isolation, persistence/restart, and docs update.
* Why it comes in this order: It validates the full composed product after feature activation.
* Main dependencies: Prior phases complete enough to test.
* Candidate work items: Multi-user E2E tests; restart persistence tests; route inventory; migration audit; docs updates for operations and architecture.
* Acceptance criteria: Normal user workflows survive browser refresh and Docker restart, and API-only surfaces have documented auth behavior.
* Risks: Stale docs can reappear if source and documentation are not verified together.

## 11. Recommended Abracapocus Planning Notes for Claude

* Recommended plan name: `CHATBOT_FULL_FUNCTIONALITY_COMPLETION`.
* Recommended phase list: Use the ten phases from Section 10.
* Recommended global execution rules: Do not change table/column names unless a migration explicitly reconciles stale schema; preserve existing auth behavior; every user/session/companion feature must have ownership acceptance criteria; every UI task must verify actual reachability from `App.tsx`; every provider task must fail clearly when unconfigured; do not expose unauthenticated admin/memory/rooms/agency/config surfaces.
* Recommended out-of-scope items: New frameworks, unrelated redesign, localStorage-only durable history, unverified provider promises, changing Vite proxy behavior unless explicitly planned.
* Dependency warnings: Memory activation depends on embeddings and Background Worker execution. Proactive agency depends on scheduler/runtime and durable storage. Voice depends on provider implementation and likely migration. Visual identity depends on migrations, provider config, and API routes. Continuity may require schema reconciliation before UI work.
* Phases that should be small/surgical: Route-prefix fixes, companion router registration, config endpoint auth ownership checks, admin auth gating, persona cache invalidation.
* Tasks that require migrations: Voice preferences if the table is truly absent; visual tables for appearances/outfits/image memories; continuity table reconciliation if existing migrations do not match runtime models.
* Tasks that require UI work: Phases 1, 3, 4, 5, 6, 7, 8, and admin parts of Phase 9.
* Tasks that require runtime/worker work: Phases 2, 3, 5, 6, and performance parts of Phase 9.
* Tasks that should have strict user-scoping acceptance criteria: Sessions/messages, companions, config, memory, rooms/scenes, agency, voice preferences, visual records, continuity records, streaming chat.
* Tasks that should verify actual UI reachability: Every task mounting an existing component; do not treat a component as done unless a normal authenticated user can navigate to it and complete the workflow.

Do not write the final Abracapocus plan skeleton from this document. This section is planning support only.

## 12. Do Not Do Yet

* Do not rely on vector memory until embedding provider config and `retrieval_engine` wiring are active.
* Do not promise proactive check-ins until a scheduler/worker is running and agency data is durable.
* Do not expose memory controls until memory routes are auth-scoped and route prefixes match the Vite proxy.
* Do not expose rooms/scenes broadly until room APIs verify current-user ownership.
* Do not expose relationship/character/identity settings until config endpoints verify session ownership.
* Do not expose admin dashboard until admin endpoints are authenticated or otherwise protected.
* Do not expose voice as functional until TTS/STT provider calls are implemented and provider config is known.
* Do not treat visual generation or understanding as working until migrations, routes, and provider wiring are proven.
* Do not treat streaming chat as production-ready until it uses auth, session ownership, history, and persistence.
* Do not build continuity UI against `/api/timeline`, `/api/narrative`, or `/api/rituals` until matching backend routers exist.
* Do not use `BackgroundQueue` as evidence that deferred work runs; a Background Worker must actually consume tasks.

## 13. Evidence Appendix

* `docs/ARCHITECTURE.md`: Existing architecture summary; useful for intent but source inspection found some runtime gaps.
* `docs/OPERATIONS.md`: Existing operations/runbook context; confirms current app ports and operational claims to verify.
* `README.md`: Not present at the repository root during inspection.
* `docker-compose.yml`: API, UI, Postgres/pgvector, and Redis services exist; no worker service exists.
* `Dockerfile`: Backend image installs the Python package and runs `uvicorn chatbot.api.app:app --host 0.0.0.0 --port 8000`.
* `react_ui/Dockerfile`: Frontend image builds Vite output and serves it with nginx.
* `pyproject.toml`: Confirms dependencies; no added provider SDKs for voice/image beyond `httpx`.
* `alembic.ini`: Alembic config points at `migrations` and leaves `sqlalchemy.url` empty.
* `migrations/env.py`: Alembic reads `get_settings().database_url`, so `.env` fallback is used for migrations.
* `chatbot/config.py`: Loads `.env`; has database, Redis, JWT, OpenRouter, default model/provider settings; lacks embeddings, voice, visual, scheduler, admin, and routing policy settings.
* `chatbot/api/app.py`: Router registration source of truth; registers auth, sessions, chat, streaming, memory, agency, rooms, admin, config, voice; no companion, continuity, or visual routers.
* `chatbot/api/auth.py`: Signup/login/logout/me routes are registered and token response includes user data.
* `chatbot/auth/dependencies.py`: Current user dependency used by active auth/session/chat routes.
* `chatbot/api/sessions.py`: Durable authenticated session list/create/get/messages/delete implementation.
* `chatbot/api/chat.py`: Authenticated non-streaming chat, DB history load, provider pipeline call, message persistence, persona cache, and background queue creation.
* `chatbot/api/streaming.py`: SSE endpoint exists but lacks auth, session ownership, DB history, and persistence.
* `chatbot/core/turn.py`: Defines full pipeline, fast-path behavior, deferred enqueue, EI, room, memory, timeline, and trace calls.
* `chatbot/providers/bootstrap.py`: Only OpenRouter is registered by default; warns when API key is missing.
* `chatbot/providers/openai_compat.py`: Implements non-streaming OpenAI-compatible chat completion calls.
* `chatbot/providers/router.py`: Provider/model routing exists as code but is not configured from settings.
* `chatbot/api/memory.py`: Memory CRUD/search/export/provenance router exists at `/api/memories`; lacks auth.
* `chatbot/memory/`: Working memory, retrieval, extraction, pgvector store, controls, search, export, and provenance code exists.
* `chatbot/consolidation/`: Summarizer, contradiction, decay, and worker modules exist.
* `chatbot/api/config.py`: Relationship, character, and identity endpoints exist but lack auth ownership checks.
* `chatbot/soul/`: Personality, prompt, mood, user state, modes, relationship, character, identity, and evolution code exists.
* `chatbot/api/agency.py`: Registered agency endpoints use in-memory scheduler/notifications/initiative objects.
* `chatbot/agency/`: Scheduler, notifications, initiative, and check-in generation scaffolds exist.
* `chatbot/api/rooms.py`: Registered rooms/scenes API exists but accepts arbitrary `user_id` and is not mounted in UI.
* `chatbot/rooms/`: Room, scene, and roleplay domain logic exists.
* `chatbot/api/voice.py`: Registered voice API exists at `/api/voice`; preferences use auth; synthesize/transcribe do not.
* `chatbot/voice/tts.py`: TTS adapter validates identifiers and cadence but provider call raises `NotImplementedError`.
* `chatbot/voice/stt.py`: STT adapter validates identifiers but provider call raises `NotImplementedError`.
* `chatbot/visual/`: Appearance, image generation, and image understanding scaffolds exist; no registered router or real backend configured.
* `chatbot/api/admin.py`: Registered admin endpoints read in-memory collectors and are not auth-gated.
* `chatbot/observability/`: Trace, metrics, and log collectors are in memory.
* `chatbot/perf/background.py`: In-process queue and worker helper exist, but no worker lifecycle was found.
* `chatbot/perf/cache.py`: In-process persona cache exists and is used by chat.
* `chatbot/db/models.py`: Contains broad SQLAlchemy model surface for auth, sessions, messages, memory, personality/config, agency, rooms, voice, visual, and observability.
* `migrations/versions/0001_core_schema.py`: Core sessions, messages, episodes, semantic facts.
* `migrations/versions/0002_emotional_state.py`: Mood/user-state session columns and logs.
* `migrations/versions/95005655ed55_add_relationship_and_character_tables.py`: Relationship, character, and identity tables.
* `migrations/versions/0004_memory_provenance.py`: Semantic fact provenance/privacy controls.
* `migrations/versions/0005_continuity.py`: Continuity tables, but names differ from some continuity module-local models.
* `migrations/versions/0006_agency.py`: Agency tables exist, but agency API uses in-memory services.
* `migrations/versions/0007_rooms_scenes.py`: Rooms/scenes tables.
* `migrations/versions/0008_auth_companions.py`: Users, user sessions, companions, and user/companion links for sessions/messages.
* `migrations/versions/0009_observability.py`: Observability tables exist, but collectors are in memory.
* `react_ui/vite.config.ts`: `/api` proxy rewrite strips the `/api` prefix.
* `react_ui/src/App.tsx`: Main app mounts only auth, session sidebar, message list, and chat input.
* `react_ui/src/api/client.ts`: Broad API client exists, including many methods without mounted UI; memory/voice and continuity path mismatches are visible.
* `react_ui/src/types.ts`: Types exist for many features beyond active chat.
* `react_ui/src/components/`: Many feature components exist but are unmounted.
* `react_ui/src/hooks/useAudioRecorder.ts`: Browser audio recorder hook exists but is not mounted.
* `tests/test_auth_companions.py`: Contains a local companions router for tests; production app registration is absent.
* `.abracapocus/plans`: Original task-group artifacts are present for historical context, but this assessment is based on current source registration and runtime wiring.
