# c

## Goal

Continuity and narrative. A relationship timeline of milestones, emotional events, promises, and running projects. A narrative threading engine producing grounded callbacks ("last week you were worried about your dad", "that sounds like the same problem from the other night") beyond naive top-k retrieval. Rituals and shared routines including morning check-in, nightly recap, weekly planning, named recurring activities, and inside jokes. The timeline and narrative threads feed the context compiler so continuity shapes every turn.

## Notes

- Async: Wave 1 (disjoint): continuity/__init__.py + timeline.py as one package task, narrative.py, rituals.py, and db/models.py continuity tables. Wave 2: the 0005 migration (depends on models.py), context_compiler.py extension to incorporate timeline and narrative threads into ranked context (depends on timeline.py and narrative.py), and client.ts extension. Wave 3: chatbot/core/turn.py adding a timeline-update step (depends on timeline.py, rituals.py, and the context_compiler task), and the TimelinePanel React component (depends on client.ts). Test fan-in last.
- Risk: context_compiler.py was a phase-d differentiator; the extension is owned by one task and must not regress existing retrieval ranking — keep the change additive. turn.py and db/models.py single-owner. The 0005 migration must be reversible with its real head revision verified at materialize time. Narrative callbacks must respect the relevance threshold from the operator answer to avoid forced references.
