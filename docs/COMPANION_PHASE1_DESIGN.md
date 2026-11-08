# Companion Evolution — Phase 1 Design: Persistent Identity and Relationship (Task 1.1)

**Repository:** `~/dev/mike/chatbot/python/chatbot` (branch `main`, after Phase 0 merge `f71e885` and `6c2f019`)
**Status:** Phase 1 (Tasks 1.1, 1.2 and 1.3) ACCEPTED for final integration, commit and push. See the approval and implementation records below; their uncommitted/task-boundary notes are historical. Original design discussion is preserved.
**Date:** 2026-11-07
**Requirements:** P01, P08, A03, A04, A07, A09, A10, A13, A15, A16. All other P/E/A items stay as registered in `COMPANION_EVOLUTION_REQUIREMENTS.md`; none is implemented here.
**Builds on:** `COMPANION_PHASE0_REFACTOR_DESIGN.md` (shared preparation `chatbot/api/conversation.py`, atomic completion, fast path preserved).

Evidence tags: **[V-code]** read in source; **[V-run]** reproduced by running the real code (no database, no network).

---

## Approval record — Task 1.2 (2026-11-07)

The owner approved D1–D6 and D10 as recommended: drift stays session-scoped; deterministic latest-session relationship backfill preserves sources and reports conflicts; existing relationship API/UI targets companion storage; factual completed turns (including roleplay) use atomic updates; companion deletion with conversations returns 409 without deleting history; timeline remains session-scoped.

Exact approved recommendations:
- **D9 — Companion-less sessions:** No durable identity or relationship.
- **D12 — IdentityControls “Evolve” and “Reset mood” buttons:** **Keep** them session-scoped (they act on the working copy).

**D11 modified:** Do not add the unused reserved `self_model` column. Defer it to Phase 3 when its data structure and update semantics are established. This supersedes the column and references proposed below.

**D7/D8 deferred:** Task 1.3 only; no relationship prompt integration or core-values removal in Task 1.2. No commit/push authorized. Original recommendations below remain as design history, superseded by this record.

---

## Approval and implementation record — Task 1.3 (2026-11-07)

**D7 — approved with clarification.**
- Use stored, evidence-based relationship depth.
- Do not advance depth because the interaction count grows: counts are factual history, not proof of intimacy.
- Never present an invented stage as fact.
- Preserve the user's configured relationship preferences.

**D8 — approved.** Remove the generic, hard-coded core values applied identically to every companion. The configured personality determines each companion's values and behavior.

**Implemented (uncommitted, on `main`).** All prompt composition goes through the shared `CompileContextStep`, which standard chat (fast and full path), SSE streaming and spoken chat all use. There is no per-mode resolution.

- **Composition:** `turn._effective_identity_inputs` composes the prompt's identity from the Task 1.2 metadata, which `load_companion_inputs` loads in its single joined query.
  - **Configured personality:** the persona block and `IDENTITY`, from `companions`, unchanged.
  - **Persistent evolved identity:** `effective_traits` (configuration ⊕ `trait_adjustments`) seeds each new session's working traits (`persistent_identity.session_identity_seed`).
  - **Persistent relationship:** `companion_relationships` is passed to `PersonaPromptAssembler.assemble(relationship_config=…)`.
    - It renders only the configured preferences (type, tone, directness, warmth, initiative, content level); `relationship_stage_known=False`, so no stage, depth or stage guidance appears (D7).
    - It adds one factual line once at least one turn has completed: "N completed exchanges to date since <first> (most recent <last>)". No closeness is inferred.
  - **Session working copy:** shown through `persistent_identity.prompt_identity_view`, a read-only view; stored rows are untouched. The view:
    - drops the legacy generic `core_values` (D8), including copies stored in older session rows;
    - drops configuration copies (`name`, `backstory`, `humor_style`) for companion sessions, because `IDENTITY` already states them and the copies go stale when the companion is edited;
    - drops relationship defaults copied into older seeds (`relationship_type`, `relationship_warmth`, `relationship_directness`, `initiative_level`) wherever a durable relationship exists. Empty tiers are no longer rendered (`IdentityState.prompt_tiers`).
- **Seeds:** `IdentityState.defaults()` no longer contains `core_values`, and new session seeds no longer copy relationship defaults. Random drift and identity mood remain session-scoped and never write to `companion_identities`.
- **Rooms and fallback:** a companion session that was never seeded (`create_room` creates one) starts from the companion's effective identity, and `UpdateIdentityStep` persists it. The generic "name: Companion" identity no longer appears. Companion-less sessions keep their previous behavior (D9).
- **Inspection:**
  - The trace prompt remains the exact provider input.
  - The `compile_context` trace step now records `identity_sources`: configured, evolved (adjustments and effective traits), relationship (settings, history, `stage_shown: false`) and session (rendered tiers, whether seeded from the companion).
  - `PromptInspector` shows them in an "Identity Sources" panel under the assembled prompt (`react_ui/src/components/identitySources.ts`).

**Evidence.**
- `tests/test_persistent_identity_integration.py`: 12 tests through the real `chat`, `stream_chat`, avatar `_spoken_turn`, `create_session` and `create_room`, on real SQLAlchemy sessions. Only the LLM provider is faked, and it records its exact inputs. The tests cover:
  - configured, evolved and relationship layers reaching the LLM;
  - no generic or duplicated identity;
  - relationship and history reused by a new session;
  - identical system prompts from standard, streaming and spoken chat;
  - room sessions;
  - companion and user isolation;
  - mood and drift never becoming durable;
  - inspector equal to the provider input;
  - fast path;
  - one generation, one embedding batch and one durable-state query per turn.
- **Mutation checks:** removing the relationship, the prompt view or the room seeding each fails the expected tests.
- **Runs:**
  - Disposable PostgreSQL 16 + pgvector built only by `alembic upgrade head`: 62/62 (Task 1.3, Task 1.2 storage, timeline migration, completion, lifecycle).
  - Default environment, focused Phase 0 + Phase 1 set: 237 passed, 19 PostgreSQL-only skipped.
  - Frontend: 79/79.
  - Wider non-integration suite: failure set identical to the committed Phase 0 baseline.
- **Overhead:** `compile_context` +18 µs median per turn. No added query, LLM call or embedding call.

**Limitations.**
- A durable adjustment reaches an *open* session only through that session's next seed, i.e. in the next session (§11). Until then, `IDENTITY` shows configured traits while the working tier shows current ones.
- Depth has no evidence source until Phase 5, so no stage is shown.
- Mood-engine response guidance ("without assuming familiarity") is unchanged (Phase 5).

---

## 1. Executive recommendation

Add **two small companion-scoped tables**, one row per companion, and change nothing else about ownership:

| Table | Holds | Why separate |
|---|---|---|
| `companion_identities` | the companion's **evolved** identity: trait adjustments relative to its configuration (empty today), a reserved self-model document for Phase 3, and a version counter | Configuration (`companions`) stays the single authority for the **initial** identity. Evolution is stored as *adjustments*, so the two can never disagree about the seed, and a user's edit to the companion still applies. |
| `companion_relationships` | the **user–companion relationship**: the existing relationship settings (today stored per session), `depth`, and factual interaction history (first and last interaction, completed turns) | The relationship stops belonging to a disposable session (A04) without becoming a per-conversation record. |

Session tables keep their current meaning: `identity_states` becomes explicitly the **session working copy** (per-session drift and mood tier), and `sessions.mood_state`/`user_state` remain **temporary emotional state**.

Three rules make the boundary explicit:

1. **Random drift is never written to durable identity.** Measured on the real `bounded_drift` as `calculate_drift` calls it: across 1,000 simulated long sessions, 31% end outside the documented ±20% band and the worst drifts 61% **[V-run]**, because the bound is re-anchored to the current value on every step. Today a new session discards it; persisting it would compound the walk across sessions.
2. **Durable identity changes only through an evidence-carrying writer**, added in Phase 3. Phase 1 creates and loads the rows; it changes their evolved content only by seeding.
3. **Task 1.2 changes no prompt text**; Task 1.3 is the only prompt change.

No new dependency, framework, background worker, LLM call or embedding call.

---

## 2. Existing state inventory

| State | Storage | Scope | Durable? | Kind | Reaches the prompt today? |
|---|---|---|---|---|---|
| Companion name, persona text, description, backstory, `personality_sliders`, humor, communication style, favorite topics, linguistic fingerprint | `companions` (`Companion`, `models.py:105`) | companion (owned by one user) | yes | **initial configuration** | yes: persona block (`companion_to_persona`, Phase 0.2) and `IDENTITY` (`companion_to_profile_dict`) **[V-run]** |
| Per-session copy of the character profile | `character_profiles` (`CharacterProfileModel`, `models.py:442`), seeded by `sessions._seed_session_from_companion` | session | until the session is deleted | copy of configuration | **no** for companion sessions (`load_companion_inputs` supplies the profile; `_persona_identity_inputs` reads the table only when none is supplied) **[V-code]** |
| Identity tiers `fixed`/`slow`/`mood` | `identity_states` (`IdentityStateModel`, `models.py:477`) | session | until the session is deleted | seed copy plus random drift plus per-session mood | yes: `FIXED IDENTITY`, `CURRENT DRIFT TRAITS`, `MOOD` sections (`persona_prompt._render_identity_sections`) **[V-run]** |
| Relationship settings (type, tone, directness, warmth, initiative, content level) | `relationship_configs` (`RelationshipConfigModel`, `models.py:407`), edited by `RelationshipSettings` on the UI's Session tab via `PUT /api/config/relationship/{session_id}` | session | until the session is deleted | **relationship, explicitly configured by the user** | **no**: never loaded by preparation and never passed to `assemble()` **[V-code]** |
| Relationship `depth` and stage | `RelationshipConfig.depth` (`soul/relationship.py`) | none | **no column exists**; always 0, so the stage is always "acquaintance" | relationship | no |
| Relationship values actually in the prompt | `identity_states.slow.relationship_warmth/_directness`, `initiative_level`, `fixed.relationship_type`, copied from `RelationshipConfig.defaults()` at seeding | session | session | defaults, not the user's settings | yes, under `CURRENT DRIFT TRAITS` and `FIXED IDENTITY` **[V-run]** |
| Bot mood, user state, conversation mode | `sessions.mood_state`, `user_state`, `conversation_mode`; logs `bot_mood_logs`, `user_state_logs` | session | session | **temporary emotion** | mood/mode modifiers in streaming/spoken (standard chat does not read saved state: Phase 0 U1) |
| Relationship timeline (milestones, promises) | `relationship_timeline_entries` (FK `session_id` ON DELETE CASCADE) | session | until the session is deleted | relationship history | no |
| Semantic facts | `semantic_facts` (`user_id` only) | **user, across all companions** | yes | user facts | no (not retrieved: Phase 2) |
| Rituals / agency | `rituals` (session string id), `agency_rituals`, `initiative_settings` (user string id) | session / user | yes | continuity | ritual prompts only |

### What resets when a new session starts **[V-code]**

`POST /sessions` (`api/sessions.py:create_session`) seeds a fresh `CharacterProfileModel` and `IdentityStateModel` from the companion's configuration via `_seed_session_from_companion`. Everything the previous session accumulated is invisible to the new one:

- slow-tier drift;
- identity mood;
- `sessions.mood_state` and user state;
- relationship settings the user chose;
- timeline entries.

Rooms created by `rooms.room.create_room` auto-create a session **without seeding**. After its first turn, `UpdateIdentityStep` falls back to `IdentityState.defaults()` (`turn._identity_model_for_update`). From then on the prompt carries `FIXED IDENTITY: name: Companion; backstory: A steady companion…` beside the real companion's identity **[V-code]**.

### What the prompt contains for a seeded companion session today **[V-run]**

Rendered with the real assembler:

- Persona block: `Identity: Mercury`, `Backstory:` (the *description*), `Directives:` (persona text).
- `IDENTITY:` from the companion.
- `FIXED IDENTITY:` repeats name, backstory and humor, and adds `core values: careful listening, practical support, clear conversation`. These come from `IdentityState.defaults()`, not from the companion.
- `relationship type: friend`.
- `CURRENT DRIFT TRAITS:` the companion's sliders, plus default values for sliders it does not set, and `relationship warmth 0.70`, `relationship directness 0.50`, `initiative level 0.30` from defaults.
- `MOOD:` (identity mood tier) and `Response guidance:` (mood-engine affect).

### Unused or disconnected

- `relationship_configs` (UI-edited, never in the prompt).
- `RelationshipConfig.depth` / `stage` (no storage).
- `character_profiles` for companion sessions.
- `CharacterCreator` UI (not mounted).
- `IdentityState.slow_baseline` and `mood_reset_target`: `calculate_drift` passes neither, so both default to the current values. Drift is therefore re-anchored on every step, and mood decay targets itself.
- `calculate_mood` replays the whole session from a default mood each turn, and its result overwrites `sessions.mood_state` written by the mood engine (Phase 0 R7).

---

## 3. Current ownership and lifecycle problems

| # | Problem | Requirement |
|---|---|---|
| L1 | Developed identity lives only in `identity_states` (FK session). A new session starts from configuration; deleting a session deletes the identity. | P01, A03 |
| L2 | The relationship lives in `relationship_configs` (FK session): one per conversation, lost with it, and never used. | P08, A04 |
| L3 | The relationship values that do reach the prompt are defaults copied into identity seeds, so there are two relationship authorities and the wrong one wins. | A07, A13 |
| L4 | The only "evolution" is an unanchored random walk (§1 rule 1). | P01, E06 |
| L5 | Unseeded sessions (rooms) get a generic default identity. | P01 |
| L6 | Seeds copy configuration (`fixed.name/backstory/humor_style`), so editing a companion leaves existing sessions with stale copies. | A15, A16 |
| L7 | Companion deletion is a raw `DELETE FROM companions` (`CompanionManager.delete`). `sessions`, `messages`, `appearances` and `image_memories` reference `companions` with no `ON DELETE` (`0697e9f7bc08`), so deleting any companion that has a conversation fails at the database. This is pre-existing. | A16 |
| L8 | `semantic_facts` are per user, not per companion: memories cross companions. Phase 2 concern, recorded here. | A07, A09 |

---

## 4. Proposed persistent identity model

`CompanionIdentityModel` → table **`companion_identities`** (new, in `chatbot/db/models.py`):

| Column | Type | Notes |
|---|---|---|
| `companion_id` | `String(36)`, **PK**, FK `companions.companion_id` **ON DELETE CASCADE** | one row per companion; ownership inherited from `companions.user_id` |
| `trait_adjustments` | `JSON` NOT NULL, default `{}` | signed numeric deltas keyed by slider trait name (`openness`, `agreeableness`, …), applied on top of the configured value and clamped to [0, 1] at read. **Empty in Phase 1.** |
| `self_model` | `JSON` NOT NULL, default `{}` | reserved for Phase 3 (values, opinions, interests, self-understanding with evidence references). **Empty and unread in Phase 1** (decision P1-D11). |
| `version` | `Integer` NOT NULL, default 1 | SQLAlchemy `__mapper_args__ = {"version_id_col": version}` |
| `created_at`, `updated_at` | `timestamptz` NOT NULL, `now()` | `updated_at` onupdate |

**Effective identity** = configuration (`Companion`) ⊕ `trait_adjustments`, computed by one pure function, `effective_traits(companion, identity) -> dict[str, float]`. It uses the companion's `personality_sliders` and the existing `DEFAULT_PERSONALITY_SLIDERS` for unset sliders, exactly as seeding does today. Nothing else from configuration is copied into the table. Name, backstory, persona text and style always come from `companions`.

Why deltas rather than a stored copy of the traits:

- The seed has exactly one authority (L6).
- "Evolved vs configured" is visible: an empty dict means the companion is unchanged.
- A configuration edit still moves the personality while keeping developed differences.
- Phase 3 gets the anchor that today's drift lacks (L4).

---

## 5. Proposed relationship model

`CompanionRelationshipModel` → table **`companion_relationships`** (new):

| Column | Type | Notes |
|---|---|---|
| `companion_id` | `String(36)`, **PK**, FK `companions.companion_id` **ON DELETE CASCADE** | one relationship per companion, and therefore per (owner, companion): `companions.user_id` is the single owner. Not one per conversation. |
| `type`, `tone`, `directness`, `warmth`, `initiative_level`, `content_level` | as in `relationship_configs` | the existing `RelationshipConfig` fields, validated by the existing dataclass |
| `depth` | `Float` NOT NULL, default 0 | the existing `RelationshipConfig.depth`, finally stored. **No writer in Phase 1** (evidence-based updates are Phase 5). |
| `first_interaction_at`, `last_interaction_at` | `timestamptz` NULL | facts, maintained from completed turns (P1-D4) |
| `completed_turns` | `Integer` NOT NULL, default 0 | count of eligible completed turns (Phase 0 semantics: not interrupted, not failed) |
| `version` | `Integer` NOT NULL, default 1 | incremented by every write |
| `created_at`, `updated_at` | `timestamptz` | |

The interaction facts are derived from conversation events, never estimated; they give later phases (P08 familiarity, Phase 5 continuity) something real to build on. `RelationshipConfig.defaults()` remains the default row content.

---

## 6. Initial personality versus evolved personality

| Layer | Authority | Written by | Phase 1 behavior |
|---|---|---|---|
| Initial identity (seed) | `companions` | user (companion editor, `CompanionManager.create/update`) | unchanged |
| Evolved identity | `companion_identities.trait_adjustments` (+ `self_model` later) | **Phase 3 evidence-based writer only** | rows exist, empty |
| Session working copy | `identity_states` | `UpdateIdentityStep` (existing random drift, mood tier), `POST /api/config/identity/{session}/evolve` | unchanged; never written back |

**Contract for Phase 3**, defined now and not implemented: one function, e.g. `apply_identity_change(db, companion_id, change, evidence)`.

- It runs in the off-path worker transaction (Phase 2).
- It locks the row with `SELECT … FOR UPDATE` (via `with_for_update()`) and applies bounded adjustments relative to configuration.
- It bumps `version`, and records evidence references (episode or message ids) inside `self_model`/provenance.

Random drift (`bounded_drift`) never calls it. Nothing in this design prescribes *what* a personality should become: no target traits, no agreeableness floor, no correction rules.

---

## 7. Session-specific versus persistent state

| State | Scope after Phase 1 | Survives session deletion | Survives restart |
|---|---|---|---|
| Configuration | companion | yes | yes |
| Evolved identity | companion | **yes** | yes |
| Relationship (settings, depth, interaction facts) | companion | **yes** | yes |
| Conversation history | session | no (owned by the session, unchanged) | yes |
| Session working identity (drift, identity mood) | session | no | yes |
| Mood, user state, mode | session | no | yes |
| Timeline entries | session (unchanged; P1-D6) | no | yes |

Nothing identity-related is held in process memory: `sessions_store` is unused, and `PersonaCache` caches prompts only when no identity inputs exist. A restart loses nothing durable.

---

## 8. Schema changes and ER diagram

```
users 1───* companions 1───1 companion_identities          (new, ON DELETE CASCADE)
              │        1───1 companion_relationships        (new, ON DELETE CASCADE)
              │
              1───* sessions 1───* messages, episodes, bot_mood_logs, user_state_logs
                          │──* identity_states            (session working copy, unchanged)
                          │──* character_profiles         (legacy copy, unchanged)
                          │──* relationship_configs       (legacy, unchanged; no longer written)
                          └──* relationship_timeline_entries (ON DELETE CASCADE, unchanged)
```

- Two new tables; no column added to, or removed from, any existing table.
- `companions` is not altered. Putting evolved state on `companions` would make every identity write contend with companion edits on one row and one `updated_at` (which the UI shows), and would mix the seed and evolution authorities.
- `relationship_configs` is not re-scoped in place. That would need a nullable `session_id`, a partial unique key and mixed legacy/current rows in one table.

---

## 9. Alembic migration strategy

One revision, `add_companion_identity_and_relationship` (Task 1.2), `down_revision = f687a630a8d1`:

1. **Refuse** if either table already exists. They are new names, so there is nothing to adopt.
2. `create_table` both tables with the columns above, and `ON DELETE CASCADE` to `companions`.
3. **Backfill identities:** `INSERT INTO companion_identities (companion_id) SELECT companion_id FROM companions ON CONFLICT DO NOTHING`. Every row gets `trait_adjustments = {}`. **No session drift is migrated** (§10).
4. **Backfill relationships:** one row per companion.
   - **Settings:** take the **most recently updated** `relationship_configs` row among sessions where `sessions.companion_id = companion` **and** `sessions.user_id = companions.user_id`. Order deterministically by `updated_at DESC, created_at DESC, id DESC` (`row_number()` partitioned by companion). Companions with no candidate get `RelationshipConfig.defaults()` values. `depth = 0`.
   - **Interaction facts:** computed from stored messages in that companion's owned sessions.
     - `first_interaction_at` / `last_interaction_at` = `min`/`max(created_at)` of user and assistant rows.
     - `completed_turns` = assistant rows whose `turn_id` has no interruption marker. A `NULL` `turn_id` counts as completed.
5. **Report, don't merge:** before inserting, run a `GROUP BY companion_id HAVING count(DISTINCT (type, tone, directness, warmth, initiative_level, content_level)) > 1` query. Log each conflicting companion with the chosen config id and the number of distinct candidates through Alembic's logger. (PostgreSQL does not allow `DISTINCT` inside window functions, so this is a separate statement.)
6. **Downgrade:** drop both tables. Legacy tables were never modified, so downgrade restores the exact prior state; durable state created after the upgrade is lost, as documented.

The revision is idempotent (`ON CONFLICT DO NOTHING`), runs in Alembic's transaction, deletes nothing, and is tested on a fresh database through `alembic upgrade head` (§17).

---

## 10. Existing-data compatibility

| Existing data | Policy | Why |
|---|---|---|
| `companions` | untouched; remains the seed authority | |
| Sessions, messages, episodes, logs | untouched | |
| `identity_states` (all sessions) | **untouched and not migrated** into durable identity | Their only non-seed content is random drift (§1). Several sessions per companion would hold different random values with no meaningful precedence. Promoting one would invent development. The rows keep working as session working copies. |
| `character_profiles` | untouched, unread for companion sessions (as today) | copies of configuration; not used for companion sessions |
| `relationship_configs` | **copied** (latest per companion, P1-D2), never modified or deleted | the user's explicit choices; conflicts are reported (§9.5) |
| Timeline entries | untouched | |
| Companions with no conversations | identity and relationship rows with defaults | |
| Sessions without a companion | no durable identity or relationship (P1-D9) | there is no one to have a relationship with |

Nothing is backfilled that did not happen: interaction facts are counts and timestamps of stored messages.

---

## 11. State-loading precedence

For a turn on a companion-bound session, preparation resolves in this order (higher wins where the same value appears twice):

1. **Configuration** (`companions`): name, persona text, description, backstory, style, fingerprint, configured sliders.
2. **Evolved identity** (`companion_identities`): `effective_traits = configured ⊕ trait_adjustments`.
3. **Relationship** (`companion_relationships`): the only authority for relationship values (Task 1.3 removes the defaults copied into session seeds: P1-D7).
4. **Session working copy** (`identity_states`): per-session drift and identity mood tier. It is seeded from (1)+(2) at session creation and never writes back.
5. **Temporary emotion** (`sessions.mood_state`, `user_state`, `conversation_mode`).
6. **Turn-computed state** (mood engine, user state, mode for this turn).

A durable change made during an open session (Phase 3) reaches that session from its next session onward. Recomputing the working copy per turn is a Phase 3 choice and needs no schema change, because both layers are stored.

---

## 12. Update and concurrency semantics

| Write | Where | Mechanism | Lost-update safety |
|---|---|---|---|
| Relationship interaction facts (P1-D4) | completion: `conversation.add_turn_messages` callers. Standard: request transaction before `db.commit()`. Streaming/spoken: the atomic messages+effects transaction in `_persist_messages(db=stream_db)`. | a single SQL statement `UPDATE companion_relationships SET completed_turns = completed_turns + 1, last_interaction_at = GREATEST(…, :now), first_interaction_at = COALESCE(first_interaction_at, :now), version = version + 1 WHERE companion_id = :c`, issued **last, immediately before the commit** | atomic in PostgreSQL; concurrent sessions serialize on the row lock only for the duration of a commit |
| Relationship settings | `PUT /api/config/relationship/{session_id}` re-targeted to the session's companion (P1-D3) | ORM update; `version` bumped | last writer wins (a human edit); no turn path writes these columns |
| Evolved identity | Phase 3 only | `SELECT … FOR UPDATE` in the off-path transaction plus `version_id_col` | row lock; the version catches out-of-band ORM writes (`StaleDataError` → re-read) |
| Session working copy | `UpdateIdentityStep` (unchanged) | per-session rows | sessions do not share rows (unchanged) |

- **Deadlock analysis:** a turn transaction touches its own `sessions` row and then the shared relationship row, always in that order, so concurrent turns contend on one shared resource and cannot form a cycle. Keeping the relationship `UPDATE` as the last statement holds its lock for the commit only. This follows the lock-ordering lesson from Phase 0.3: never write a row in one session while another open transaction in the same task holds it.
- **Locks:** none in preparation (reads only) and none across generation or playback. No global or application-level lock.

---

## 13. Deletion and ownership behavior

| Action | Behavior |
|---|---|
| Delete a session (`DELETE /sessions/{id}`) | Unchanged: messages and session-owned rows go. `companion_identities` and `companion_relationships` are **not affected**: they have no FK to sessions. Interaction counts are history and are not decremented. |
| Delete a companion | New tables cascade from `companions`, so they never orphan. Today the delete fails at the database for any companion with conversations (L7). Recommended (P1-D5): keep conversations safe. `CompanionManager.delete` checks for sessions and returns **409** with a clear message, instead of a raw database error. Cascading the deletion to conversations is a data-deletion decision for the owner. |
| Delete a user | no such path exists; nothing to change |
| Ownership | All reads and writes go through the companion, filtered by `companions.user_id = current user`, as `CompanionManager.get` and `load_companion_inputs` already do (A09). Rooms do not partition identity: the same companion is the same person in every room. Scene isolation (`SceneMemoryGateStep`) still governs memories. |

---

## 14. Integration with Phase 0 conversation preparation

- **Load:** `conversation.load_companion_inputs` already fetches the companion once per turn (`CompanionManager.get`). Task 1.2 replaces that fetch with one ORM query: `select(Companion, CompanionIdentityModel, CompanionRelationshipModel)` with two `LEFT JOIN`s on `companion_id` and the same ownership filter. The function then returns, from one round trip:
  - the profile and persona (unchanged);
  - `companion_identity` (effective traits);
  - `companion_relationship`.

  `build_turn_context` places them in `ctx.metadata`. **Statement count per turn is unchanged.**
- **Seed:** one function, `initial_session_identity(companion, identity) -> IdentityState`, replaces the body of `sessions._seed_session_from_companion`. With empty adjustments its output is byte-identical to today's seed. Task 1.3 also uses it for unseeded room sessions and as `_identity_model_for_update`'s fallback (L5).
- **Prompt (Task 1.3 only):** `turn._persona_identity_inputs` returns `relationship_config` as a third input, and `_assemble_persona_prompt` passes it to `PersonaPromptAssembler.assemble(relationship_config=…)`, which already supports it. The trace and prompt inspector record which layer supplied each section (`ctx.metadata["identity_sources"]`, A13).
- **Complete:** the relationship `UPDATE` (§12) joins the existing completion transactions. No new transaction, task or worker.
- **Fast path:** `FAST_PATH_STEPS` still runs `update_identity` (session working copy only), and completion still records the interaction. No step is added to any path.

---

## 15. Task 1.2 implementation specification: storage, migration, loading

**Objective:** durable identity and relationship rows exist for every companion, load through shared preparation and survive sessions and restarts. **No prompt text changes.**

**Files:**
- `chatbot/db/models.py` (two models)
- `migrations/versions/<new>_add_companion_identity_and_relationship.py`
- `chatbot/companions/companion.py` (`create` inserts both rows in its transaction; `delete` per P1-D5)
- `chatbot/api/conversation.py` (`load_companion_inputs` single query, `build_turn_context` metadata, relationship-interaction update helper)
- `chatbot/api/chat.py` and `chatbot/api/streaming.py` (call the helper next to `add_turn_messages`)
- `chatbot/api/sessions.py` (`_seed_session_from_companion` → `initial_session_identity`)
- `chatbot/api/config.py` (relationship endpoints re-targeted to the companion row: P1-D3)
- tests

**Steps:**
1. Add `CompanionIdentityModel` and `CompanionRelationshipModel` (§4–5) to `chatbot/db/models.py`, which `migrations/env.py` imports, so they are inside Alembic's metadata (unlike the timeline model, which lives in `chatbot/continuity/timeline.py`).
2. Add the migration (§9).
3. `CompanionManager.create`: insert both rows (defaults) in the same transaction as the companion.
4. `load_companion_inputs`: one joined query; a missing row (impossible after migration) is treated as defaults on read, never created on the read path.
5. Add `record_completed_interaction(db, companion_id, at)`, the single-statement update of §12. Call it immediately before the commit in standard chat and inside `_persist_messages` when it owns the turn's transaction. Eligible completed turns only (P1-D4).
6. Relationship API: resolve `session_id → sessions.companion_id` with ownership checked, then read and write `companion_relationships`. The response schema and URL are unchanged, so the UI needs no change. Companion-less sessions keep the legacy session-scoped behavior.
7. `initial_session_identity` used by `create_session`; output identical to today when adjustments are empty.

**Acceptance:**
- Fresh `alembic upgrade head` creates both tables, and the backfill is correct on seeded data.
- A new session and a restarted process load the same durable rows.
- Deleting a session leaves them; deleting a companion leaves no orphans.
- A **golden prompt for a fixed fixture is byte-identical before and after**.
- Preparation statement count is unchanged; the fast path is intact.
- Concurrent completed turns lose no counts on PostgreSQL.

---

## 16. Task 1.3 implementation specification: prompt integration and precedence

**Objective:** the effective identity and relationship reach the prompt with the explicit precedence of §11; the inspector shows the source of each layer; the seed stays legible.

**Files:** `chatbot/core/turn.py` (`_persona_identity_inputs`, `_assemble_persona_prompt`), `chatbot/api/conversation.py`, `chatbot/api/sessions.py` and `chatbot/rooms/room.py` (seeding through `initial_session_identity`), tests.

**Steps (each prompt-visible; P1-D7 and P1-D8 must be decided first):**
1. Pass `relationship_config` from `companion_relationships` to the assembler. **This is the first time the user's relationship settings reach the model.**
2. Stop copying relationship defaults (`relationship_warmth`, `relationship_directness`, `initiative_level`, `relationship_type`) into new session seeds, so relationship values have one authority.
3. Seed unseeded sessions (rooms, the `_identity_model_for_update` fallback) from the companion's effective identity instead of `IdentityState.defaults()` (L5).
4. Per P1-D8, stop injecting the hard-coded `core_values` default into the fixed tier.
5. Record `identity_sources` in turn metadata and the trace.

**Acceptance:**
- Equal prompts across standard, streaming and spoken for the same state (Phase 0 test A extended).
- Golden prompts reviewed and approved for:
  - a configured companion;
  - a companion with relationship settings;
  - a room session.
- No change in statement count or latency.

---

## 17. Targeted regression tests

PostgreSQL tests reuse the Phase 0 infrastructure: `PHASE0_PG_URL` pointing at a disposable `pgvector/pgvector:pg16` instance, a fresh database per test, and `alembic upgrade head` run as a subprocess (`tests/test_timeline_migration.py`).

| Test | Proves |
|---|---|
| Fresh `upgrade head` creates both tables; Alembic autogenerate reports no differences | schema matches the models |
| Backfill on a seeded pre-migration database: companions with zero, one and conflicting `relationship_configs`; sessions owned by another user ignored; latest-wins order; the conflict report emitted; legacy rows unchanged | migration policy (§9–10) |
| Backfill leaves `trait_adjustments = {}` even when `identity_states` hold drift | no drift promotion |
| Migration re-run / downgrade / upgrade | idempotent, reversible |
| New session after drift in an old one: the new seed equals configuration ⊕ adjustments, not the old session's drift | P01 boundary |
| New engine and session factory (restart) reads identical rows | A03 |
| Delete session → durable rows remain; delete companion (per P1-D5) → no orphans | §13 |
| Two concurrent completed turns on two sessions of one companion → `completed_turns` +2 | §12 |
| User B cannot read or write user A's companion relationship through the API or preparation | A09 |
| Relationship API through a session id persists to the companion and is visible from a second session | A04 |
| Preparation statement count unchanged; golden prompt unchanged (Task 1.2) | A10, no prompt change |
| `test_standard_fast_path_is_intact_and_defers_extraction` and Phase 0 suites unchanged | fast path, Phase 0 |
| Task 1.3: cross-mode prompt equality with the relationship included; room-session seed; inspector `identity_sources` | A13 |

Existing suites to keep green:
- `tests/test_identity_config.py` (20)
- `tests/test_soul.py` (9)
- `tests/test_config_integration.py` (9)
- `tests/test_auth_companions.py` (43, including the delete and ownership tests)
- `tests/test_completion_persistence.py`, `tests/test_conversation_lifecycle.py`, `tests/test_conversation_preparation.py`
- `tests/test_session_delete_cascade.py` (needs a seeded built-in voice)

---

## 18. Performance considerations

- **Preparation:** the companion fetch becomes a three-table `LEFT JOIN` on primary keys, replacing the current single query. Zero added round trips, and nothing on the first-text path grows by more than a primary-key join.
- **Completion:** one primary-key `UPDATE` inside an existing transaction, issued just before its commit. It is post-response for streaming/spoken and inside the existing request transaction for standard chat. No new commit, connection, task or network call.
- **No new LLM or embedding calls.** No locks in preparation; none across generation or playback.
- **Fast path, SSE incrementality, spoken streaming, provider formatting and embedding batching are untouched.**

---

## 19. Unresolved product decisions

| ID | Decision | Recommendation |
|---|---|---|
| P1-D1 | Session random drift | **Keep** it session-scoped and unchanged in Phase 1 (existing functionality); never persist it; retire it in Phase 3.3 |
| P1-D2 | Migrating existing per-session relationship settings | **Adopt the latest per companion**, report conflicts, keep legacy rows. Alternative: start everyone at defaults. |
| P1-D3 | Where relationship settings are edited | **Keep the Session-tab UI and URL**, re-targeted to the companion's relationship (no frontend change). A later UI move to the Companion tab is optional. |
| P1-D4 | Maintain interaction facts (`completed_turns`, first/last interaction) from Phase 1 | **Yes**: factual, cheap, needed by P08/Phase 5 |
| P1-D5 | Deleting a companion that has conversations | **409 and keep conversations** (today: database error). Alternative: delete the conversations too (destructive). |
| P1-D6 | Timeline (promises, milestones) scope | **Stay session-scoped until 3.1** (shared experiences); deleting a conversation still deletes its timeline |
| P1-D7 | Relationship in the prompt (Task 1.3) | Render the user's settings. With `depth` fixed at 0 the existing fragment always says "Stage: Acquaintance… keep interactions polite, relatively safe, and exploratory": **omit the stage line until depth is evidence-based** |
| P1-D8 | Hard-coded `core_values` ("careful listening, practical support, clear conversation") injected into every companion's fixed identity | **Remove in Task 1.3.** It is not the companion's configuration and pushes every character toward a helper persona. |
| P1-D9 | Companion-less sessions | No durable identity or relationship |
| P1-D10 | Roleplay-scene turns | Count toward interaction facts; whether in-character turns may change real identity is a Phase 3 decision |
| P1-D11 | Reserve `self_model` now | **Yes** (one empty JSON column avoids a Phase 3 schema change); alternatively add it in Phase 3 |
| P1-D12 | `IdentityControls` "Evolve" and "Reset mood" buttons | **Keep** them session-scoped (they act on the working copy) |

Out of scope and recorded: memories crossing companions (`semantic_facts` per user, L8, Phase 2); identity evolution content (Phase 3); emotional continuity across sessions (Phase 5).
