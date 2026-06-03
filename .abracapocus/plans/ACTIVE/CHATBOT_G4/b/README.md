# b

## Goal

Visual identity and image. An image-generation and image-understanding provider interface. Generation with an appearance/character seed lock for visual continuity, customizable appearance, and outfit continuity. Event-based images that become image memories in shared history. Multimodal image understanding for user-sent images. API endpoints and UI for avatar display and image messages.

## Notes

- Async: Wave 1 (disjoint): visual/__init__.py + base.py as one package task, appearance.py (canonical appearance + outfit continuity), and db/models.py image-memory and appearance tables. Wave 2: generation.py (depends on base.py and appearance.py), understanding.py (depends on base.py), the 0010 migration (depends on models.py), and client.ts extension. Wave 3: api/images.py endpoints (depends on generation and understanding), api/app.py registering the images router (single-owner, depends on images.py), and the AvatarDisplay and ImageMessage React components (depend on client.ts). Test fan-in last.
- Risk: Image model identifiers and the appearance-consistency method must be validated before execution per the operator answer. db/models.py and api/app.py single-owner. The 0010 migration must be reversible with its real head revision verified at materialize time. Image memories must integrate with the existing memory/provenance model rather than a parallel store.
