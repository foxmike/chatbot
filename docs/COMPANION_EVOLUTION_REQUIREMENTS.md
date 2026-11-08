# Companion Evolution — Master Requirements Register

**Project:** `~/dev/mike/chatbot/python/chatbot`  
**Status:** Planning baseline; no implementation authorized by this document  
**Source of truth:** This register, revised only with explicit decisions.  
**Last consolidated:** 2026-11-07

## 1. Product objective

Create persistent, distinctive, believable *simulated* companions whose identity and relationships develop through meaningful conversations. The user sets a compact initial character seed; experience, reflection, memory, and behavioral choices make the character individual over time. The goal is **a person-like character that is interesting to talk to**, not a generic assistant with a detailed configuration form. Support diverse temperaments, independent opinions, disagreements, humor, and distinctive expression without systematically steering characters toward a single agreeable personality.

A successful companion remembers important events, interprets them through its own temperament, acts consistently with developed values and motivations, and remains recognizable across sessions and application restarts. The system should be understandable and testable without becoming a heavy governance framework.

## 2. Decision vocabulary and change control

| Marker | Meaning |
|---|---|
| **IN** | Agreed target direction; exact implementation still requires design and approval. |
| **REVIEW** | Preserved candidate; decide include, defer, or exclude during its phase. |
| **DEFER** | Explicitly retained for possible later implementation. |
| **OUT** | Explicitly excluded by an identified product decision. |
| **DONE** | Implemented and accepted with evidence. |

**Important:** The user agreed that some unspecified ideas could be left out permanently but did **not** name those ideas individually. Therefore **no specific idea is marked OUT by inference**. Every original item remains visible until a named decision is recorded. This is a register, not a promise to implement every experimental idea.

### Approved decisions to preserve

- **Phase 0 precedes personality work:** consolidate common identity/context/memory/persistence/post-turn behavior across standard, streaming, and spoken chat. Keep distinct transport and speech-specific behavior.
- **Restore all three advanced capabilities:** competing motivations (**E02**), selective attention (**E03**), and private character interpretation (**E10**) are **IN**.
- **Minimal user configuration:** the character's initial seed provides identity; development is primarily experience-driven.
- **No additional synchronous LLM calls** in the conversational critical path without explicit approval. Prefer bounded, batched, durable, off-path processing.
- **Production-intended design, no throwaway bridges**, no broad refactors, and no new frameworks/dependencies without approval.
- **Engineering integrity only:** data ownership, transaction correctness, provenance, idempotency, and resource bounds. Do **not** invent subjective behavioral or relationship restrictions, ideological personality corrections, or a large governance subsystem.
- Preserve existing LLM routing, prompt inspection, embedding batching/latency gains, streaming, audio/AVTR behavior, and saved companion data unless an approved task requires a narrowly scoped change.
- Plan and execute interactively using focused Claude Code/AGY/Pi tasks, **not** an overnight `/goal` loop; do not auto-commit/push.

## 3. Original 16 personality capabilities — complete inventory

These are preserved from `personality_ideas.docx` in their original order. "Phase" is the likely home, not an implementation design.

| ID | Capability | Intended behavior / acceptance evidence | Status | Phase |
|---|---|---|---|---|
| P01 | Persistent self-identity | Values, quirks, habits, preferences and developed traits persist across sessions/restarts; companion remains recognizable. | **IN** | 1, 3 |
| P02 | Behavioral personality engine | Traits affect *choices* (challenge, explain, joke, tease, disagree), not just prompt adjectives. | **IN** | 4 |
| P03 | Emotional continuity | Simulated mood/reaction persists appropriately across turns and informs dialogue rather than resetting every reply. | **IN** | 5 |
| P04 | Independent opinions | Character forms and retains positions, can disagree, and can revise positions for coherent reasons. | **IN** | 3, 4 |
| P05 | Conversation initiative | Character occasionally introduces relevant topics/questions and can steer conversation naturally. | **IN** | 4, 5 |
| P06 | Character-specific speech fingerprint | Recognizable humor, vocabulary, rhythm, storytelling and imperfections; may begin as generated/configured seed and adapt gradually. | **IN** | 6 |
| P07 | Reflection and self-development | Important experiences lead to recorded, explainable adjustments to the character's understanding of itself. | **IN** | 3 |
| P08 | Relationship model | Rapport, familiarity, trust, shared experiences, unresolved topics and interaction preferences persist coherently. | **IN** | 1, 3, 5 |
| P09 | Personal goals and interests | Character maintains topics it wants to explore, unfinished interests and bounded fictional goals. | **IN** | 4 |
| P10 | Autobiographical continuity | Memories form a coherent character/user shared history, not isolated fact snippets. | **IN** | 2, 3 |
| P11 | Selective forgetting | Significant memories persist; low-value detail is condensed, deprioritized or becomes ineligible for recall without accidental data loss. | **REVIEW** | 2 |
| P12 | Contradictions and imperfections | Stable quirks, weaknesses and tensions can coexist without incoherent random drift. | **REVIEW** | 3, 4 |
| P13 | Topic-specific enthusiasm | Reactions visibly vary according to persistent personal interests and mood. | **IN** | 4, 5 |
| P14 | Social awareness | Interpret humor, criticism, teasing, pauses and social context in character rather than merely classifying sentiment. | **IN** | 5 |
| P15 | Shared traditions | Recognize and appropriately revisit running jokes, rituals and meaningful recurring experiences. | **REVIEW** | 3, 5 |
| P16 | Adaptive conversational pacing | Natural choices among brief acknowledgment, question, story, explanation, pause and topic shift; not merely audio speed. | **IN** | 4, 6 |

## 4. Ten original experimental ideas — none silently omitted

| ID | Experimental idea | Scope and constraints | Status | Phase |
|---|---|---|---|---|
| E01 | Personality inertia | Development reflects accumulated evidence; stable anchors change slowly, meaningful experiences can matter. | **IN** | 3 |
| E02 | **Competing motivations** | Multiple values can conflict (e.g., honesty versus tact); character-specific tradeoffs influence behavior. No dedicated rules engine assumed. | **IN — explicitly restored** | 4 |
| E03 | **Selective attention** | Character notices and gives weight to different aspects of the same input based on its interests/history. No extra inference call assumed. | **IN — explicitly restored** | 4 |
| E04 | Relationship-specific behavior | Different familiarity/rapport produces natural changes while core identity remains intact. | **IN** | 5 |
| E05 | Narrative self-reflection | Revisit previous judgments and maintain coherent development over time. | **IN** | 3 |
| E06 | Earned opinions | Views emerge from simulated experiences and meaningful conversations, not random trait mutations. | **IN** | 3, 4 |
| E07 | Character-specific memory bias | Differences in *interpretation/attention* may exist, but underlying stored events/facts must not be falsified. | **REVIEW** | 3, 4 |
| E08 | Personality fingerprints (evaluation) | Detect generic-assistant drift and measure distinctiveness through behavioral tests. | **IN** | 7 |
| E09 | Synthetic formative experiences | Optionally generate coherent fictional formative episodes when creating a new companion; distinguish seeded fiction from observed history. | **REVIEW** | 1, 3 |
| E10 | **Private character interpretation** | A compact, non-user-facing situation/behavioral interpretation informs dialogue; prefer same generation over new blocking LLM request. | **IN — explicitly restored** | 4 |

## 4A. Six architectural approaches from the original document — explicitly mapped

The original `personality_ideas.docx` also proposes six longer-form approaches; they are **not** replaced by the tables above:

| Source label | Original approach | Tracked requirements / status |
|---|---|---|
| A | Companion has a model of *itself*, not only of the user | **IN**: P01, P07, E05, A03, A11. |
| B | Simulated internal state combines stable temperament with momentary reactions | **IN**: P03, P13, P14, A11. State represents simulation, not subjective feeling. |
| C | Personality-driven decisions before natural dialogue, ideally without extra model calls | **IN**: P02, E02, E03, E10, A18. |
| D | Persistent curiosity and unfinished conversational business | **IN**: P05, P09, P16. |
| E | Autobiographical memory rather than isolated facts | **IN**: P10, P07, E05, A07. |
| F | Distinctive conversational style that can be measured | **IN**: P06, E08, A14. |

## 5. Additional product/engineering requirements

| ID | Requirement | Acceptance principle | Status | Phase |
|---|---|---|---|---|
| A01 | Unified conversation semantics | Same identity, context, retrieval, prompt inputs, persistence, and eligible post-turn side effects for equivalent completed turns in all modes. | **IN** | **0** |
| A02 | Delivery separation | Regular HTTP, SSE streaming and avatar WebSocket retain their necessary transport, streaming and interruption behaviors. | **IN** | 0 |
| A03 | Persistent evolved identity | Distinguish initial persona from long-lived evolved companion state; never reset solely because a new session starts. | **IN** | 1 |
| A04 | Persistent relationship state | User/companion interaction history is not owned by a disposable session. | **IN** | 1 |
| A05 | Semantic extraction | Replace brittle regex-only memory selection with meaningful interpretation of explicit and implicit experiences; evaluate precision/recall. | **IN** | 2 |
| A06 | Durable background work | Jobs survive restart, retry safely and process idempotently with bounded concurrency and cost. Prefer existing PostgreSQL. | **IN** | 2 |
| A07 | Distinct memory types | Keep user facts, companion self, shared events, relationship state and temporary emotion meaningfully separate. | **IN** | 1–3 |
| A08 | Memory reconciliation | Track evidence and provenance; detect corrections, merge duplicates and supersede outdated claims without treating interpretations as facts. | **IN** | 2, 3 |
| A09 | Session/room/user isolation | Enforce existing tenancy, room/scene boundaries and deletion semantics. | **IN** | All |
| A10 | Practical resource discipline | Bounded token/cost/queue/storage growth; preserve latency; no speculative extra calls or frameworks. | **IN** | All |
| A11 | Character state -> dialogue | Stable identity + current emotion + retrieved experiences affect conversational decision and wording. | **IN** | 3–6 |
| A12 | Expressive output metadata | Spoken dialogue remains clean; optional structured emotion/tone/pacing/performance metadata can be mapped to verified TTS controls later. | **IN** | 6 |
| A13 | Prompt visibility | Preserve inspectable final LLM context; distinguish actual provider payload from its display rendering. | **IN** | 0, All |
| A14 | Cross-session/long-run evaluation | Test recognizability, accuracy, individuality, continuity, response latency and cost over repeated sessions. | **IN** | 0, 7 |
| A15 | Configuration simplicity | Initial character seed + optional advanced controls; do not require users to micromanage personality vectors or emotion sliders. | **IN** | 1, 3–6 |
| A16 | State integrity without behavioral policing | Use normal transactions, version checks, provenance and recovery; do not install subjective personality/relationship restrictions. | **IN** | All |
| A17 | Conversational personality maturity | Distinct from first interaction, increasingly individualized through meaningful experience, not a mandatory linear maturity score. | **REVIEW** | 3–5 |
| A18 | No new blocking planning step by default | Competing motivations, attention and interpretation should first be delivered using existing-model prompt/structured generation and measured for TTFT impact. | **IN** | 4 |

## 6. Explicit non-goals / boundaries

- No separate replacement chatbot stack, second orchestration framework or large policy/governance engine.
- No avatar rendering, TTS/STT model, voice-library or UI redesign as part of Phase 0. Later expressive performance metadata is allowed **only** within approved scope.
- No canned conversation library or scripted personality masquerading as emergence; deterministic code is acceptable for state management and integrity.
- No requirement for a separate model call per turn for interpretation, reflection or emotional classification.
- No inferred mandatory "safe" relationship personality, enforced agreeableness or automatic ideological moderation feature. Product-specific behavior restrictions require explicit user decisions; existing legal/platform obligations remain separate.
- No unapproved data backfill or automatic database deletion. "Selective forgetting" requires a precise product decision: query eligibility/summary reduction versus permanent erasure.
- No new permanent exclusions without identifying the exact IDs in a decision log.

## 7. Standing performance and quality rules

1. Preserve the observed Mercury real-turn reduction from roughly **9.01s to 2.04s** STT-complete-to-first-browser-text; embedding-prep baseline **7.63s -> 0.87s** after batching. These are historical measurements, **not promises for every model/turn**.
2. No new synchronous external LLM calls on the critical path without explicit approval and measured net benefit.
3. Batch off-path extraction/reflection when useful; enforce job budgets, retries, idempotency and backpressure.
4. No hidden requirement that a character converge to a more agreeable/default assistant personality.
5. Every phase needs behavioral evidence plus targeted regression tests; do not confuse tests of data storage with demonstrated personality quality.
6. Character memories and conclusions should carry provenance/evidence sufficient for correction; do not equate fictional persona history with observed real-world events.

## 8. Source register

- `personality_ideas.docx` (user-provided): 16 priority capabilities, six conceptual approaches, ten experiments, and three-character/30-scenario evaluation proposal.
- `docs/COMPANION_MEMORY_CONTEXT_AUDIT.md`: initial implementation inventory (agent-generated; verify code claims).
- `docs/COMPANION_EVOLUTION_PLANNING_RESEARCH.md`: detailed pre-plan code audit (agent-generated; verify code claims and metric naming).
- Conversation decisions through 2026-11-07, including explicit restoration of E02, E03, E10 and exclusion of unsolicited behavioral restrictions.

## 9. Decision log and carry-forward rules

| Date | Decision | IDs |
|---|---|---|
| 2026-11-07 | Phase 0 is mandatory before feature expansion. | A01–A02 |
| 2026-11-07 | Restore competing motivations, selective attention, private interpretation. | E02, E03, E10 |
| 2026-11-07 | Technical integrity only; no invented subjective behavioral governance. | A09, A16 |
| 2026-11-07 | Avoid new blocking model calls; favor bounded asynchronous processing. | A06, A10, A18 |
| 2026-11-07 | Some experiments may later be excluded; specific IDs not decided. | E07, E09, P11, P12, P15 and any others only by explicit review |

**Maintenance rule:** Before each coding prompt, cite affected requirement IDs and record completed/deferred/changed IDs afterward. Never replace this register with a summarized subset. Keep initial ideas visible, even if rejected later.
