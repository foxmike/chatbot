# Companion Evolution — Phase 3.2 Self-Model Implementation Contract

**Status:** DESIGN, awaiting owner approval of §10. No production code. Implemented by Task 3.3.
**Requirements:** P01, P04, P07, E01, E06, A16 (plus A07/A09/A10 as constraints).
**Authorities:** `COMPANION_EVOLUTION_REQUIREMENTS.md`, `COMPANION_EVOLUTION_ARCHITECTURE.md`, `COMPANION_EVOLUTION_IMPLEMENTATION.md`; Phase 1 contract in `COMPANION_PHASE1_DESIGN.md` §4, §6 (deferred `self_model`, D11); Phase 2 durable extraction on `main` (`98f19aa`).
**Scope:** what the companion believes about itself and how that changes. User facts stay in Phase 2 memory, relationship depth stays in Phase 5, and shared-experience records stay in Task 3.1.

---

## 1. Summary

The self-model is one bounded JSON document added to the existing `companion_identities` row. It holds developed opinions, preferences, interests and self-understanding, each with evidence. It is updated only by the existing durable `memory_extract` job, as part of that job's existing atomic effects transaction. The update is a deterministic accumulator: no new model call, queue, worker or framework. Candidate observations come from the **same** extraction model call that already runs once per completed turn. Configuration remains the identity anchor. Trait changes keep using the existing `trait_adjustments`, now through an evidence-gated, slow, capped writer. The self-model reaches the prompt through the existing joined companion query and the shared `CompileContextStep`, adding no query and no model call.

## 2. Components reused (exact)

| Need | Reuse | Change in 3.3 |
|---|---|---|
| Durable identity row, owner, cascade, version | `CompanionIdentityModel` (`chatbot/db/models.py`, table `companion_identities`, `version_id_col`) | add `self_model` column (§3) |
| Anchors (initial identity) | `Companion` configuration: persona text, description/backstory, `personality_sliders`, humor, communication style, `favorite_topics` | none; never written by evolution |
| Effective traits | `effective_traits()` in `chatbot/soul/persistent_identity.py` (configuration ⊕ `trait_adjustments`, clamped) | none |
| Off-path execution, retries, idempotency | `BackgroundJob` + `DurableJobWorker` + `_process_memory_extract_job` (`chatbot/perf/durable_handlers.py`): the receipt is cached in `payload.extraction_result`, effects are applied under the job-row lock, and `effects_applied` and success are committed atomically | extend the receipt and the effects step (§5) |
| Observation source | `LLMMemoryExtractor` (`chatbot/memory/llm_extraction.py`): one bounded call per completed turn, grounded validation, heuristic fallback | add a second output list (§4.1) |
| Text guards | `_CONDITIONAL`, `_FICTION`, `_JOKE`, `_QUOTED_SPAN`, `_ASTERISK_SPAN`, `_ROLEPLAY_FRAME` (`extraction.py` / `llm_extraction.py`) | reuse; the hedge guard is **not** applied to companion opinions ("I think…" is how opinions are stated) |
| Prompt read path | `load_companion_inputs` / `_load_companion_state` (`chatbot/api/conversation.py`), already a single joined `Companion ⟕ CompanionIdentityModel ⟕ CompanionRelationshipModel` query | add `durable_state["companion_self_model"]` |
| Prompt composition and inspection | `_effective_identity_inputs` (`chatbot/core/turn.py`) → `PersonaPromptAssembler` → `_render_identity_sections` (`chatbot/soul/persona_prompt.py`); `identity_sources` trace → `PromptInspector` | one new labelled section and one inspector entry |
| Scope and isolation | `Message.companion_id`, `Room.persist_to_durable`, `Room.scene_id`, job `user_id`/`companion_id` ownership check | reuse as eligibility (§4.2) |

**Not touched:** session drift (`bounded_drift`, `calculate_drift`, `UpdateIdentityStep`), retired in Task 3.3 separately; relationship tables; Phase 2 memory tables; the completion hook and job scheduling; providers and routing.

## 3. Data representation

**Migration (one, additive):** `companion_identities.self_model JSON NOT NULL DEFAULT '{}'`, on top of head `d41e8a7c502b`. This is the column D11 deferred to "when its data structure and update semantics are established", which this document does. An empty `{}` means "no development", and the prompt is then byte-identical to today's. Downgrade refuses to drop a non-empty `self_model`, following the Phase 2 evidence rule.

**Document, schema 1** (written only by `apply_self_observations`, §5):

```json
{
  "schema": 1,
  "items": {
    "<item_key>": {
      "kind": "opinion | preference | interest | self_understanding",
      "subject": "modal jazz",
      "statement": "I find modal jazz more rewarding than bebop.",
      "reason": "the space between notes leaves room to think",
      "status": "tentative | held",
      "strength": 0.4,
      "support": 1.0,
      "opposition": 0.0,
      "sessions": ["<session_id>", "..."],
      "first_at": "<iso>", "updated_at": "<iso>",
      "evidence": ["<evidence_ref>", "..."],
      "history": [{"statement": "...", "reason": "...", "until": "<iso>", "evidence_ids": ["..."]}]
    }
  },
  "traits": {
    "<slider>": {"net": 0.0, "sessions": ["..."], "evidence": ["<evidence_ref>"], "last_applied_at": null}
  },
  "log": [{"at": "<iso>", "change": "held | revised | trait", "key": "...", "delta": 0.02, "evidence_ids": ["..."]}]
}
```

- `item_key` = `kind:` + the normalized subject (casefolded, punctuation and stopwords removed, at most 64 chars). The extractor is shown the existing keys, so it reuses them (§4.1).
- **evidence_ref** = `{id, job_id, turn_id, session_id, message_id, source_at, weight, excerpt}`.
  - `id` is deterministic: `uuid5(job_id, index)`.
  - `excerpt` is at most 160 chars of the **companion's own** reply text. User text is never copied into it.
- **Caps** (constants, no configuration):

  | Element | Cap | When it is reached |
  |---|---|---|
  | Items per kind | opinion 24, preference 16, interest 16, self-understanding 8 | evict tentative items first; never evict held ones. A new candidate stays out, and this is logged |
  | Evidence per item or trait | 8 | keep the first plus the 7 most recent |
  | `history` per item | 5 | |
  | `sessions` per item | 10 | |
  | `log` | 50 | |
  | Serialized document | 64 KiB | the update is rejected and the job fails visibly; nothing is truncated silently |

- **Values** are not a developed kind: they stay in the configured persona (anchor, §10 S6).

## 4. Evidence

### 4.1 Observation candidates (same extraction call)

`LLMMemoryExtractor` returns an additional list, `companion`, in its existing JSON response. There are at most 4 per turn.

```json
{"kind": "opinion | preference | interest | self_understanding | trait_signal",
 "subject": "...", "statement": "...", "reason": "...",
 "evidence": "<verbatim span of the ASSISTANT reply>",
 "status": "real | roleplay | hypothetical | joke",
 "change_of_mind": false,
 "significance": "ordinary | significant",
 "trait": "<slider name, trait_signal only>", "direction": 1}
```

- The prompt input gains the existing held and tentative `item_key: statement` lines, at most 1,200 chars, loaded in the job's existing read transaction before inference.
- Validation is deterministic and mirrors the user-memory checks, applied in this order:
  1. `status == "real"`.
  2. `evidence` is a verbatim span of the **assistant** reply. For `trait_signal`, it may instead be a span of the user message describing an experience.
  3. Names and numbers in `statement`/`reason` appear in the turn.
  4. The roleplay, conditional, fiction, joke and quoted/asterisk guards pass on the evidence sentence.
  5. `trait` is a key of `DEFAULT_PERSONALITY_SLIDERS` and `direction ∈ {-1, 1}`.
- **Echo rule:** an `opinion`/`preference` whose statement repeats the user's stated position (≥ 80% significant-word overlap with the user message) and whose `reason` is not grounded in the assistant reply is rejected. Agreement is not an earned view (E06).
- Accepted observations are stored in the receipt as `extraction_result.companion_observations`, next to the existing `entries` and **not** counted in its `[:8]` cap. Retries replay this list exactly.
- On fallback (provider error, timeout, malformed output) there are no observations. The heuristic extractor never produces them.

### 4.2 Eligibility (all required)

- The turn completed normally: the existing condition for scheduling the job, so interrupted and failed turns are excluded.
- The job's session is companion-bound. The assistant `Message.companion_id` equals the job's `companion_id`, which is the attribution for multi-companion rooms.
- If the session belongs to a room: `persist_to_durable` is true, and the room has no `scene_id` (in-character scenes are fiction).
- Weight: `ordinary` = 1, `significant` = 2. **At most 2 weight per item (or trait) per session**, so one long conversation cannot reach a threshold alone.
- Never evidence:
  - mood or sentiment;
  - user instructions about the companion's personality ("be more sarcastic"): those are configuration or relationship requests;
  - user claims about what the companion likes, unless the companion endorses them with its own reason;
  - assistant replies produced under an explicit roleplay frame.

### 4.3 Thresholds

| Change | Qualifying evidence | Rule |
|---|---|---|
| **Develop opinion** | companion states a position with a grounded `reason` | first reasoned observation → `held`, strength 0.4. Unreasoned → `tentative`; it becomes `held` at support ≥ 2 across ≥ 2 sessions |
| **Reinforce** opinion/preference/interest | consistent observation on the same key | support += weight; strength += 0.1 at most once per session, cap 0.9 |
| **Revise opinion** | (a) `change_of_mind=true` with a grounded reason that differs from the current reason, **or** (b) opposing support ≥ max(2, current support) across ≥ 2 sessions | the old statement/reason moves to `history`; the new one becomes `held` at strength 0.4; opposition resets. User insistence without companion-stated reasons never counts (P04: coherent reasons) |
| **Develop preference** | as interest | |
| **Develop interest** | sustained engagement or an explicit "I've become interested in…" | support ≥ 3 across ≥ 2 sessions → `held`. A subject in configured `favorite_topics` is an anchor: no item is created |
| **Self-understanding** | an explicit self-reflection in the reply | support ≥ 2 → `held`. A single `significant` reflection qualifies (P07: important experiences matter) |
| **Adjust trait** | `trait_signal` with significance `significant` only | net weight in one direction ≥ 4, across ≥ 3 sessions, spanning ≥ 7 days → `trait_adjustments[trait] += 0.02·direction`, then `net` and that trait's evidence are reset |
| **Leave unchanged** | anything else: chatter, single ordinary signals, out-of-scope or ineligible turns, rejected observations, opposite signals that cancel (`net` sums ±), an observation older than the item's `updated_at` for a revision (late jobs reinforce or oppose only, never overturn a newer stance) | no write; `version` unchanged |

Time and session counts use `source_at` (the message time), not processing time, so out-of-order job completion gives the same result.

## 5. Update contract

```python
async def apply_self_observations(
    db: AsyncSession,
    *,
    companion_id: str,
    observations: Sequence[SelfObservation],   # validated receipt entries
    source: EvidenceSource,                    # job_id, turn_id, session_id, message_id, source_at
    now: datetime,
) -> SelfModelChange                           # items/traits changed; empty when nothing qualified
```

- Location: `chatbot/soul/self_model.py`, a pure accumulator plus this one database function. It is called by `_process_memory_extract_job` after `TurnMemoryStorage.persist` and before `effects_applied`/`succeeded` are set, in the **same** transaction.
- Steps:
  1. `SELECT companion_identities … WHERE companion_id = :id FOR UPDATE` (`with_for_update()`). There is no row (legacy companion) → insert the default row, as creation does.
  2. Apply the pure function `next_self_model(doc, traits, observations, source, now) -> (doc, trait_adjustments, change)`.
  3. Write only if `change` is non-empty. The ORM `version_id_col` bumps `version` and rejects a stale write.
- **Idempotency:** effects run at most once per job, through the existing job-row lock plus `effects_applied`. Evidence ids are deterministic, so replaying a receipt is a no-op.
- **Lock order:** `background_jobs` row → `companion_identities` row, always. Jobs for the same companion serialize on the identity row. Other companions are unaffected.
- **Failure:** any error, including a `StaleDataError` from the version check, rolls back the whole effects transaction. The job retries with the existing backoff; once attempts are exhausted it stays `failed` with `last_error`. Self-model work never blocks or hides memory work in a separate transaction.

## 6. Personality inertia

- **Anchors** (configuration) are never written by evolution. A user edit to the companion still applies, because evolution is stored as adjustments.
- Traits move only through §4.3 "Adjust trait":
  - 0.02 per step;
  - cumulative |adjustment| ≤ **0.15** per trait relative to configuration;
  - at most one step per trait per 14 days.

  At the cap, further signals are recorded and not applied. Reaching one step takes significant experiences in three sessions over a week, so personality is recognisable at any point (E01).
- Opinions and interests change by recorded replacement with history, never by numeric drift. There are no stages, no maturity levels, and no target personality (E01: no mandatory stages; A16: no behavioural policing).
- Random session drift never writes `self_model` or `trait_adjustments`. This is an existing rule and stays a test.
- No automatic decay. Removal happens only through the caps' eviction of tentative items.

## 7. Persistence and attribution

| Concern | Contract |
|---|---|
| Transaction owner | `_process_memory_extract_job`'s effects transaction, which also owns the memory effects and the job status. The conversation path never writes the self-model. |
| Writers | Only `apply_self_observations`. No API writes it in Phase 3 (§10 S10). |
| Concurrency | Row lock (serialization) plus `version_id_col` (stale-write rejection). Readers use plain MVCC reads with no lock. A prompt may lag one completed job; that is acceptable. |
| Ordering | `source_at`-based rules (§4.3); late jobs cannot overturn newer stances. |
| Attribution | Every item, trait signal and log entry references evidence ids, which resolve to job/turn/session/assistant-message and the companion's excerpt. `log` makes every applied change explainable (P07). |
| Ownership and isolation | The companion is owned by one user; the job's `user_id`/`companion_id` are verified against the session (existing). Companion-less sessions have no self-model. |
| Deletion | Companion deletion cascades (existing). Session deletion leaves companion state, per Phase 1. Evidence refs may then dangle; the companion-authored excerpt remains (§10 S7). |
| Recovery | `log` plus the evidence ids allow manual reversal. There is no automatic rollback engine. |

## 8. Prompt integration

- **Read:** `load_companion_inputs` adds `durable_state["companion_self_model"] = self_model_prompt_view(identity.self_model)`. This is a pure function in `persistent_identity.py`, reading the same row from the same single query.
- **What is shown:** `held` items only, ordered by kind, then strength, then `updated_at` (deterministic). At most 6 opinions, 4 preferences, 4 interests and 3 self-understanding statements, in ≤ 900 chars total. Tentative items, evidence, history and the log never reach the prompt.
- **Render:** `_effective_identity_inputs` passes the view, and `_render_identity_sections` emits one section, `DEVELOPED SELF`, directly after `IDENTITY`:

  > Views and interests you have developed through your own experiences. Your configured identity above takes precedence; you may still change your mind for good reasons.
  > - Opinion — modal jazz: I find modal jazz more rewarding than bebop (because the space between notes leaves room to think).

  The section is omitted when the view is empty.
- **Precedence:** configuration > developed self > session working copy > mood. Trait adjustments keep reaching the prompt through the existing session seed (`initial_session_identity`), that is, from the next session. Self-model items apply from the next turn.
- **Modes:** standard (fast and full path), SSE and spoken all go through the shared `CompileContextStep`, so the prompts are identical.
- **Inspection:** the `identity_sources.evolved` trace entry gains `{"self_model_version": n, "shown": [item_key, …]}` for `PromptInspector`.
- **Cost:** no added query, model call or embedding call on the interactive path. At most about 900 chars of prompt.

## 9. Acceptance criteria (focused tests for Task 3.3)

1. **Inertia under chatter:** 200 completed small-talk turns over many sessions → `self_model == {}`, `trait_adjustments` and `version` unchanged.
2. **Earned opinion:** one reasoned stance → a `held` item with the full evidence ref. The next turn's prompt shows it identically in chat, SSE and spoken mode, and the inspector lists it. An unreasoned stance stays tentative and is not shown.
3. **Echo and pressure:** stances that echo the user, and repeated user insistence without companion reasons, cause no item and no revision. A reasoned `change_of_mind` revises the item and keeps the old stance in `history`.
4. **Interest threshold:** support 3 in one session → still tentative (per-session cap). Support from a second session → `held`. A configured favourite topic creates no item.
5. **Trait inertia:** signals in one session or within 7 days never apply. Meeting the threshold applies exactly ±0.02. Across 1,000 simulated qualifying events, |adjustment| ≤ 0.15 and the 14-day cooldown hold. Opposite signals cancel.
6. **Exclusions:** rejected without effects:
   - roleplay, hypothetical, joke, quoted or asterisk evidence;
   - scene rooms and `persist_to_durable=false` rooms;
   - a mismatched `Message.companion_id`;
   - companion-less sessions;
   - heuristic fallback turns.
7. **Idempotency:** crashes after the receipt and after the effects, and a duplicate claim → changes applied once, `version` +1 once (PostgreSQL fixture).
8. **Concurrency:** two jobs for one companion in parallel → both applied, no lost update. Jobs for different companions don't wait on each other.
9. **Ordering:** an older job completing after a newer revision does not restore the old stance.
10. **Bounds:** caps hold, the document stays ≤ 64 KiB, the prompt section stays ≤ 900 chars and is deterministic.
11. **Compatibility:** empty `self_model` → prompt byte-identical to the Phase 1 golden fixture. Configuration edits still apply. Migration upgrade, downgrade and refusal behaviour all tested.
12. **Call budget:** interactive turn = 1 generation, 0 new queries. Background = still 1 extraction call per completed turn.

## 10. Decisions requiring owner approval

| # | Decision | Recommendation |
|---|---|---|
| S1 | Add `companion_identities.self_model` (the deferred D11 column) rather than a separate table | **Approve**: one row, existing version and cascade; bounded by caps |
| S2 | Get observations from the existing per-turn extraction call. Output bound 700 → 900 tokens; input +≤ 1,200 chars of existing self-model keys. Rejected alternative: a periodic reflection call | **Approve**: no new call; the alternative needs separate cost approval |
| S3 | Threshold and cap constants in §3, §4.3 and §6 (0.02 step, 0.15 cap, 14-day cooldown, ≥ 4 weight / 3 sessions / 7 days) | Approve as starting constants; code constants, not settings |
| S4 | A first reasoned opinion is `held` immediately (retaining what the companion actually said), while revision needs stronger evidence | **Approve** (P04 "forms and retains") |
| S5 | Trait adjustment is enabled in Phase 3, rather than opinions and interests only | Approve with S3 bounds, or defer traits to keep the first slice smaller |
| S6 | Values stay configuration anchors; no developed-values kind | **Approve** |
| S7 | After session deletion, keep derived items and companion-authored excerpts (user text is never stored) | Approve; revisit with P11 (still REVIEW) |
| S8 | Exclude scene rooms and non-durable rooms from self-model evidence | **Approve** |
| S9 | Tentative items never reach the prompt | **Approve** |
| S10 | No user-facing view, edit or reset API in Phase 3; recovery through `log` | Approve, or add a read-only inspector view later |

## 11. Out of scope

- Shared-experience records (3.1). Evidence refs carry `turn_id`/`message_id` now; an `episode_id` field can be added when 3.1 lands, without changing the schema version.
- Retiring random drift (3.3).
- Relationship depth (Phase 5).
- Behavioural interpretation (Phase 4).
- Contradiction and imperfection modelling (P12 review).
- Any new worker, queue, framework, governance or censorship component.
