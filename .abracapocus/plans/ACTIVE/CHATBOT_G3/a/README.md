# a

## Goal

Agency and proactivity. A scheduler that decides when to initiate from rituals, timeline, and initiative settings. Proactive check-in generation grounded in recent context. A notifications model with a pollable delivery queue (reminders, emotional check-ins, continuation prompts, milestone/event callbacks). Per-user initiative controls from never-proactive to high-energy. Reuses the background-worker pattern from phase f for scheduling.

## Notes

- Async: Wave 1 (disjoint): agency/__init__.py + scheduler.py as one package task, checkins.py, notifications.py, initiative.py, and db/models.py agency tables. Wave 2: the 0006 migration (depends on models.py), api/agency.py endpoints (depends on checkins/notifications/initiative), and client.ts extension. Wave 3: api/app.py registering the agency router (depends on agency.py), and the NotificationFeed and InitiativeSettings React components in parallel (depend on client.ts). Test fan-in last.
- Risk: For v1, notifications are a pollable in-app feed unless the operator opts into push infrastructure, which is out of this group's scope. db/models.py and api/app.py single-owner. The 0006 migration must be reversible with its real head revision verified at materialize time. Proactive generation must not spam; respect the initiative-level default from the operator answer.
