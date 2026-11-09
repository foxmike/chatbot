# Phase 7.2b — Performance and Cost Evaluation (A10, A13, A14, A18)

**Status:** measurement complete; one harness fix, no behavioral code changes required.
**Method:** scripted providers only (no network, no paid inference); token counts are
**characters/4 estimates** (no tokenizer dependency added); call counts and query counts are
measured directly. Timing medians over ≥20 samples on throwaway SQLite and a disposable
Alembic-built PostgreSQL 16+pgvector instance (per the established `PHASE0_PG_URL` convention;
the development database was not touched).

**Reproducibility:** `python scripts/phase7_measurements.py`
(`PHASE0_PG_URL=...` adds the PostgreSQL campaign) writes
`docs/verification/phase7_performance_metrics.json`. The invariant tests in
`tests/test_phase7_performance_evaluation.py` (11 passing) assert the same bounded-call,
retry-safety, prompt-bound and extraction-capacity facts against scripted providers.

---

## 1. Measured latency — separated by category

### A. Deterministic application overhead (per turn, medians)

| Component | SQLite | PostgreSQL | Queries |
|---|---|---|---|
| `load_companion_inputs` (identity+relationship+open threads) | 1.11 ms | 1.33 ms | 3 |
| Cross-session mood restoration (`load_restorable_mood`+`restore_mood`) | 0.83 ms | 0.93 ms | 2 |
| Emotional-association retrieval + render | 1.57 ms | 1.60 ms | 2 |
| Persona prompt assembly (all Phase 1–3 sections) | 0.08 ms | 0.08 ms | 0 |
| Expression context (behavior→fingerprint + Phase 6.1b pacing) | — | 0.14 ms | 0 |
| `build_character_behavior` (Phase 4 block) | 0.03 ms | 0.03 ms | 0 |
| `build_open_thread_context` | 0.01 ms | 0.01 ms | 0 |
| Linguistic fingerprint format | 0.013 ms | 0.013 ms | 0 |
| Pacing guidance (Phase 6.1b) | 0.035 ms | 0.037 ms | 0 |
| Mood update (Phase 5.1b) | 0.013 ms | 0.013 ms | 0 |
| Streaming parser, 44 KB scripted segment stream | 36 ms (1.23 MB/s) | — | 0 |
| Completed-response performance-metadata pipeline, 44 KB | 23 ms | — | 0 |

**Total deterministic overhead per turn ≈ 3.7–4.2 ms median** (sum of the DB-bound and
assembly components above) — far below any user-perceptible threshold. First-text latency is
therefore dominated by provider inference (category B), which was deliberately not measured
live (no paid calls); Phase 0's historical 2.04 s Mercury measurement remains the reference
point, and phases 1–6 added no new synchronous provider call to that path (verified: one
generation + one embedding batch per turn).

### B. Provider inference (per turn, measured call counts)

- Conversation generation: **1 call/turn** (unchanged since Phase 0).
- Embeddings: **1 batch/turn** (batching retained; the historical 7.63 s → 0.87 s prep win
  is untouched).
- Structured performance metadata: parsed in-stream (23 ms per 44 KB completed response);
  no extra model call.

### C. Background extraction (durable job, scripted provider)

| Metric | SQLite | PostgreSQL |
|---|---|---|
| Provider calls per job | 1 | 1 |
| Handler wall time per job (median) | 57 ms | 22 ms |
| Extraction input prompt | 755–762 chars ≈ 190–226 est. tokens | same |
| Extraction output (scripted full-category response) | 1,457 chars ≈ 364–374 est. tokens | same |
| Shared extraction system prompt | ≈ 1,844 est. tokens input per job | same |

---

## 2. Throughput (controlled synthetic, small N)

| Workload | SQLite | PostgreSQL |
|---|---|---|
| 10 concurrent `load_companion_inputs` | 21 ms wall | 108 ms wall |
| 20 sequential durable extraction jobs | 17.3 jobs/s | 46–65 jobs/s |
| Queue backlog behavior | bounded by `(task_type, turn_id)` identity + lease/attempt limits | same |

The PG concurrent-preparation number is dominated by per-session connection setup in the
harness, not by query cost (each call is 3 indexed queries); the per-call medians above are the
meaningful figure. PostgreSQL processes extraction jobs ~2.7× faster than SQLite here.

---

## 3. Cost (token counts and call counts; no dollar estimates)

Per conversation turn (measured): 1 generation call, 1 embedding call, 1 extraction call whose
input is ≈ 226 est. tokens (turn texts + known-state blocks) plus the shared ≈ 1,844 est.-token
system instruction, and output bounded by the 700-token limit.

**Prompt growth from Phases 3–6 (measured on the real renderers, est. tokens):**

| Section | Chars | Est. tokens |
|---|---|---|
| Phase 1 persona baseline | 1,800 | 450 |
| + relationship | 256 | 64 |
| Phase 3 developed self-model | 359 | 85–90 |
| Phase 4 character behavior block | 420 | 105 |
| Phase 5 emotional associations | 441 | 110 |
| Phase 4 open threads (when relevant) | 232 | 58 |
| Phase 6 expression (fingerprint + behavior + pacing rhythm) | ≈ 1,000 | 249–252 |
| **Full persona prompt** | ≈ 4,090 | **≈ 967–1,022** |

Growth is bounded by design (every section has a hard character cap) and totals ≈ 500 est.
tokens added since the Phase 1 baseline — material but modest against a typical context window.

---

## 4. Extraction capacity (700-token output budget, evidence)

Representative scripted full-category responses (semantic facts + experiences +
self-observations + threads + emotional associations), tokens estimated at chars/4 — for
JSON-dense output this is a *lower* bound, so the deficit is if anything understated:

| Scripted turn | Est. tokens | Fits 700? |
|---|---|---|
| Quiet smalltalk (nothing to record) | 19 | yes |
| Typical: 1–2 facts only | 106 | yes |
| Typical mixed: 2 facts + 1 experience + 1 self | 275 | yes |
| Typical rich: + threads + emotions | 419 | yes |
| Resolution turn (keys referencing known state) | 376 | yes |
| **Maximal legitimate turn (8 facts, 2 experiences, 3 self, 2 threads, 2 emotions)** | **1,063** | **no** |

Measured behavior when the budget is exceeded: the provider cuts the response
(`finish_reason=length`); the JSON becomes unparseable, `parse_candidates` returns `None`, and
the **entire response** falls back to the regex heuristic — keeping only plain user facts and
**losing all four auxiliary categories** (experiences, self-observations, threads, emotional
associations) for that turn. There is no partial parse and no model retry; the job still
succeeds with heuristic facts (verified end-to-end). Retry safety is intact: replaying a
succeeded job costs **0 extra provider calls** (durable receipt).

**Recommendation (evidence-based, not applied):** raise `LLMExtractionLimits.max_output_tokens`
from **700 to 1,100** (covers the measured maximal legitimate turn at 1,063 est. tokens with a
small margin; `max_response_chars` 12,000 already accommodates it). The failure mode is
all-or-nothing, so an occasional over-budget turn currently discards four memory categories —
a correctness loss, not just an efficiency one. Alternatively, trimming per-item JSON field
verbosity in the prompt could shave ~15%, but the budget increase is the specific, measured
adjustment. Requires explicit approval per the standing rule; the limit was **not** changed here.

---

## 5. Performance regressions found

1. **Extraction output budget vs five categories** — the one material, demonstrated issue
   (section 4). Evidence recorded; adjustment recommended, not applied.
2. `evolution.calculate_mood` replays the full conversation history every turn (measured
   0.8 ms at 50 turns, 13.5 ms at 800 turns). Bounded by `max_context_turns` (default 50) on
   the live path; not material today. Recorded as a watch item only.
3. `build_open_thread_context` treats any message containing crisis vocabulary
   (`exhausted`, `overwhelmed`, …) as priority-current-message and defers all open threads —
   intentional and conservative, but broad; worth a product look if threads feel "invisible".

## 6. Optimizations applied

- Harness-only: the initial `QueryCounter` over-counted (listener not removable via
  `event.listen`'s return value); fixed so reported query counts are exact (3/2/2 per turn).
- No product code changes were justified: every measured component is already bounded
  (queries per turn: 3 + 2 + 2; prompt sections capped; extraction single-call; retries
  receipt-guarded). Nothing was "optimized" at the cost of functionality.

## 7. Remaining bottlenecks / risks

- Provider inference remains ~99% of first-text latency; only verified-pricing cost reporting
  can be added later (no invented dollars here).
- The extraction system prompt (≈1,844 est. tokens input per completed turn) is the largest
  background cost; acceptable at one call/turn, worth watching if categories grow again.
- `calculate_mood`'s O(history) replay (watch item).
- Concurrent-preparation wall time on PostgreSQL is connection-setup dominated in the harness;
  real deployment pools connections, so per-call medians (≈1.3 ms) are the operative numbers.
