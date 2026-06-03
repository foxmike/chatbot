# c

## Goal

Authentication, per-user data isolation, and multiple companions. User accounts with credential storage and token issue/verify, a FastAPI current-user dependency, and consistent user_id scoping across sessions and chat. Multiple companions per user with private per-companion memory and optional shared room memory. Formalizes the user_id partitioning deferred at schema time. UI for auth and companion selection.

## Notes

- Async: Wave 1 (disjoint): auth/__init__.py + models.py as one package task, security.py, companions/__init__.py + companion.py as one package task, and db/models.py auth/companion tables plus user_id foreign keys. Wave 2: auth/dependencies.py current-user (depends on auth models + security), the 0008 migration (depends on models.py), api/auth.py signup/login/logout (depends on auth models + security), and client.ts auth token handling. Wave 3: api/app.py registering the auth router and applying the auth dependency (depends on dependencies.py and auth.py), api/sessions.py scoping to current user and companion (depends on dependencies.py and companion.py), api/chat.py scoping to current user and companion (depends on dependencies.py and companion.py) — these three are disjoint files — and the AuthScreen and CompanionSelector React components (depend on client.ts). Test fan-in last.
- Risk: Highest-risk phase in the group: retrofitting auth and user_id scoping onto a schema that began single-tenant. user_id partitioning must be applied consistently across every query path or data leaks across users. sessions.py, chat.py, app.py, and db/models.py are each single-owner. The 0008 migration must be reversible with its real head revision verified at materialize time and must backfill or default user_id for existing rows safely. Validate the auth model choice from the operator answer before running the phase.
