# a

## Goal

Relationship configuration and identity evolution. A relationship config model (type, tone, directness, warmth, initiative level, content level as a configurable setting). Character creation/customization feeding the soul core (name, backstory, personality sliders, humor style, communication style, favorite topics). Slow controlled identity evolution distinguishing fixed identity, slowly-changing identity, and temporary mood. UI surfaces for character creation and relationship settings. Extends the persona prompt assembly finalized in phase J.

## Notes

- Async: Wave 1 (disjoint): relationship.py, character.py, evolution.py, and db/models.py field additions. Wave 2: the 0003 migration (depends on models.py), persona_prompt.py extension to consume relationship/character/evolution (depends on the three soul modules), api/config.py endpoints (depends on relationship.py and character.py), and react_ui/src/api/client.ts extension. Wave 3: api/app.py registering the config router (depends on config.py), and the two React components CharacterCreator and RelationshipSettings in parallel (depend on client.ts). The test is the final fan-in.
- Risk: persona_prompt.py is extended again after phase J; it is owned by exactly one task here and phase J must be accepted first. db/models.py and api/app.py are each single-owner. The 0003 migration must be reversible and its real revision number verified against the current Alembic head at materialize time, since earlier groups may have advanced it. Evolution logic must bound drift from the fixed baseline per the operator answer.
