from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.agency.checkins import CheckIn, CheckInGenerator, CheckInType
from chatbot.agency.initiative import InitiativeLevel
from chatbot.agency.initiative import InitiativeSettings as AgencyInitiativeSettings
from chatbot.agency.initiative import ProjectInitiative
from chatbot.agency.notifications import Notification, NotificationQueue
from chatbot.agency.scheduler import ProactiveAction, ScheduledEvent, Scheduler

TEST_USER_ID = "user-test-abc"
TEST_USER_ID_2 = "user-test-def"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _future_seconds(seconds: int) -> datetime:
    return _utcnow() + timedelta(seconds=seconds)


def _past_seconds(seconds: int) -> datetime:
    return _utcnow() - timedelta(seconds=seconds)


def _iso() -> str:
    return _utcnow().isoformat()


class _MockInitiative:
    def __init__(self, level: str = "medium") -> None:
        self._level = level

    def get_initiative_level(self, user_id: str) -> str:
        return self._level


class TestScheduler:
    @pytest.fixture
    def scheduler(self) -> Scheduler:
        return Scheduler()

    def test_should_initiate_never(self, scheduler: Scheduler) -> None:
        scheduler.set_initiative_level(TEST_USER_ID, "never")
        assert scheduler.should_initiate(TEST_USER_ID) is False

    def test_should_initiate_quiet_hours(self, scheduler: Scheduler) -> None:
        now = datetime.now(timezone.utc).time()
        scheduler.quiet_hours_start = time(0, 0)
        scheduler.quiet_hours_end = time(23, 59)
        scheduler.set_initiative_level(TEST_USER_ID, "high")
        assert scheduler.should_initiate(TEST_USER_ID) is False

    def test_should_initiate_when_allowed(self, scheduler: Scheduler) -> None:
        scheduler.quiet_hours_start = time(3, 0)
        scheduler.quiet_hours_end = time(4, 0)
        scheduler.set_initiative_level(TEST_USER_ID, "high")
        assert scheduler.should_initiate(TEST_USER_ID) is True

    def test_get_next_event_returns_earliest(self, scheduler: Scheduler) -> None:
        early = ScheduledEvent(
            event_id="ev-1",
            user_id=TEST_USER_ID,
            event_type="checkin",
            scheduled_at=_future_seconds(60),
            action_context={},
        )
        later = ScheduledEvent(
            event_id="ev-2",
            user_id=TEST_USER_ID,
            event_type="reminder",
            scheduled_at=_future_seconds(120),
            action_context={},
        )
        scheduler.add_event(later)
        scheduler.add_event(early)

        nxt = scheduler.get_next_event()
        assert nxt is not None
        assert nxt.event_id == "ev-1"

    def test_get_next_event_ignores_past(self, scheduler: Scheduler) -> None:
        past_event = ScheduledEvent(
            event_id="ev-3",
            user_id=TEST_USER_ID,
            event_type="checkin",
            scheduled_at=_past_seconds(300),
            action_context={},
        )
        scheduler.add_event(past_event)
        assert scheduler.get_next_event() is None

    def test_get_next_event_empty(self, scheduler: Scheduler) -> None:
        assert scheduler.get_next_event() is None

    def test_add_remove_event(self, scheduler: Scheduler) -> None:
        event = ScheduledEvent(
            event_id="ev-4",
            user_id=TEST_USER_ID,
            event_type="ritual",
            scheduled_at=_future_seconds(30),
            action_context={},
        )
        scheduler.add_event(event)
        assert scheduler.get_next_event() is not None

        scheduler.remove_event("ev-4")
        assert scheduler.get_next_event() is None

    def test_remove_event_nonexistent(self, scheduler: Scheduler) -> None:
        event = ScheduledEvent(
            event_id="ev-5",
            user_id=TEST_USER_ID,
            event_type="ritual",
            scheduled_at=_future_seconds(30),
            action_context={},
        )
        scheduler.add_event(event)
        scheduler.remove_event("nonexistent")
        assert scheduler.get_next_event() is not None

    def test_run_loop_stops_on_event(self, scheduler: Scheduler) -> None:
        async def _run() -> None:
            scheduler._stop_event.set()
            task = asyncio.create_task(scheduler.run_loop())
            await asyncio.wait_for(task, timeout=2)
            assert task.done()

        asyncio.run(_run())

    def test_decide_action_returns_action(self, scheduler: Scheduler) -> None:
        scheduler.quiet_hours_start = time(3, 0)
        scheduler.quiet_hours_end = time(4, 0)
        scheduler.set_initiative_level(TEST_USER_ID, "high")
        event = ScheduledEvent(
            event_id="ev-10",
            user_id=TEST_USER_ID,
            event_type="checkin",
            scheduled_at=_future_seconds(10),
            action_context={"title": "test"},
        )
        scheduler.add_event(event)
        action = scheduler.decide_action(TEST_USER_ID)
        assert isinstance(action, ProactiveAction)
        assert action.user_id == TEST_USER_ID

    def test_decide_action_returns_none_when_no_event(self, scheduler: Scheduler) -> None:
        scheduler.quiet_hours_start = time(3, 0)
        scheduler.quiet_hours_end = time(4, 0)
        scheduler.set_initiative_level(TEST_USER_ID, "high")
        assert scheduler.decide_action(TEST_USER_ID) is None


class TestCheckInGenerator:
    @pytest.fixture
    def generator(self) -> CheckInGenerator:
        ini = _MockInitiative(level="medium")
        return CheckInGenerator(initiative=ini)

    @pytest.fixture
    def never_generator(self) -> CheckInGenerator:
        ini = _MockInitiative(level="never")
        return CheckInGenerator(initiative=ini)

    @pytest.fixture
    def reminders_only_generator(self) -> CheckInGenerator:
        ini = _MockInitiative(level="reminders_only")
        return CheckInGenerator(initiative=ini)

    def test_generate_never_returns_none(self, never_generator: CheckInGenerator) -> None:
        result = never_generator.generate("reminder", TEST_USER_ID)
        assert result is None

    def test_generate_reminders_ok_for_reminders_only(
        self, reminders_only_generator: CheckInGenerator
    ) -> None:
        result = reminders_only_generator.generate("continuation_prompt", TEST_USER_ID)
        assert isinstance(result, CheckIn)
        assert result.checkin_type == "continuation_prompt"
        assert result.user_id == TEST_USER_ID

    def test_generate_emotional_none_for_reminders_only(
        self, reminders_only_generator: CheckInGenerator
    ) -> None:
        result = reminders_only_generator.generate("emotional_checkin", TEST_USER_ID)
        assert result is None

    def test_generate_returns_checkin(self, generator: CheckInGenerator) -> None:
        result = generator.generate("reminder", TEST_USER_ID)
        assert isinstance(result, CheckIn)
        assert result.checkin_type == "reminder"
        assert result.user_id == TEST_USER_ID
        assert result.checkin_id != ""

    def test_dispatch_routes_correctly(self, generator: CheckInGenerator) -> None:
        reminder = generator.generate("reminder", TEST_USER_ID)
        assert reminder is not None
        assert reminder.checkin_type == "reminder"

        emotional = generator.generate("emotional_checkin", TEST_USER_ID)
        assert emotional is not None
        assert emotional.checkin_type == "emotional_checkin"

        continuation = generator.generate("continuation_prompt", TEST_USER_ID)
        assert continuation is not None
        assert continuation.checkin_type == "continuation_prompt"

        milestone = generator.generate("milestone_callback", TEST_USER_ID, context="ctx")
        assert milestone is not None
        assert milestone.checkin_type == "milestone_callback"

        event_cb = generator.generate("event_callback", TEST_USER_ID, context="evt")
        assert event_cb is not None
        assert event_cb.checkin_type == "event_callback"

    def test_dispatch_unknown_type_returns_none(self, generator: CheckInGenerator) -> None:
        result = generator.generate("bogus_type", TEST_USER_ID)
        assert result is None

    def test_get_recent_context_empty_catches_error(
        self, generator: CheckInGenerator
    ) -> None:
        ctx = generator.get_recent_context(TEST_USER_ID)
        assert ctx == []

    def test_milestone_callback_with_reminders_only_basic(
        self, reminders_only_generator: CheckInGenerator
    ) -> None:
        result = reminders_only_generator.generate("milestone_callback", TEST_USER_ID)
        assert isinstance(result, CheckIn)
        assert result.checkin_type == "milestone_callback"
        assert "milestone" in result.message.lower()

    def test_milestone_callback_never_returns_none(
        self, never_generator: CheckInGenerator
    ) -> None:
        result = never_generator.generate("milestone_callback", TEST_USER_ID)
        assert result is None

    def test_event_callback_never_returns_none(
        self, never_generator: CheckInGenerator
    ) -> None:
        result = never_generator.generate("event_callback", TEST_USER_ID)
        assert result is None

    def test_event_callback_with_context(self, generator: CheckInGenerator) -> None:
        result = generator.generate("event_callback", TEST_USER_ID, context="test context")
        assert isinstance(result, CheckIn)
        assert result.checkin_type == "event_callback"
        assert "test context" in result.message

    def test_continuation_prompt_reminders_only_allowed(
        self, reminders_only_generator: CheckInGenerator
    ) -> None:
        result = reminders_only_generator.generate(
            "continuation_prompt", TEST_USER_ID
        )
        assert isinstance(result, CheckIn)

    def test_generate_reminder_reminders_only_returns_none(
        self, reminders_only_generator: CheckInGenerator
    ) -> None:
        result = reminders_only_generator.generate("reminder", TEST_USER_ID)
        assert result is None


class TestNotificationQueue:
    @pytest.fixture
    def queue(self) -> NotificationQueue:
        return NotificationQueue()

    @staticmethod
    def _make_notification(
        user_id: str = TEST_USER_ID,
        notif_type: str = "reminder",
        notif_id: str | None = None,
        created_at: str | None = None,
    ) -> Notification:
        return Notification(
            notification_id=notif_id or f"n-{_utcnow().timestamp()}",
            user_id=user_id,
            notif_type=notif_type,
            title=f"Test {notif_type}",
            body=f"Body for {notif_type}",
            created_at=created_at or _iso(),
        )

    def test_enqueue_never_gated(self, queue: NotificationQueue) -> None:
        notif = self._make_notification()
        result = queue.enqueue(TEST_USER_ID, notif, initiative_level="never")
        assert result is False
        assert queue.pending_count(TEST_USER_ID) == 0

    def test_enqueue_reminders_only_types(self, queue: NotificationQueue) -> None:
        reminder = self._make_notification(notif_type="reminder", notif_id="r1")
        assert queue.enqueue(TEST_USER_ID, reminder, initiative_level="reminders_only") is True

        emotional = self._make_notification(
            notif_type="emotional_checkin", notif_id="e1"
        )
        assert (
            queue.enqueue(TEST_USER_ID, emotional, initiative_level="reminders_only")
            is False
        )

        milestone = self._make_notification(notif_type="milestone", notif_id="m1")
        assert (
            queue.enqueue(TEST_USER_ID, milestone, initiative_level="reminders_only")
            is True
        )

    def test_dequeue_fifo_order(self, queue: NotificationQueue) -> None:
        now = _utcnow()
        n1 = self._make_notification(
            notif_id="n1",
            created_at=(now + timedelta(seconds=1)).isoformat(),
        )
        n2 = self._make_notification(
            notif_id="n2",
            created_at=(now + timedelta(seconds=2)).isoformat(),
        )
        n3 = self._make_notification(
            notif_id="n3",
            created_at=now.isoformat(),
        )
        queue.enqueue(TEST_USER_ID, n1, initiative_level="medium")
        queue.enqueue(TEST_USER_ID, n2, initiative_level="medium")
        queue.enqueue(TEST_USER_ID, n3, initiative_level="medium")

        result = queue.dequeue(TEST_USER_ID)
        assert result is not None
        assert result.notification_id == "n3"

    def test_dequeue_skips_read_and_dismissed(self, queue: NotificationQueue) -> None:
        n1 = self._make_notification(notif_id="n1", created_at=_iso())
        n1.read = True
        n2 = self._make_notification(
            notif_id="n2", created_at=(_utcnow() - timedelta(seconds=1)).isoformat()
        )
        n2.dismissed = True
        n3 = self._make_notification(
            notif_id="n3", created_at=(_utcnow() + timedelta(seconds=1)).isoformat()
        )
        queue.enqueue(TEST_USER_ID, n1, initiative_level="medium")
        queue.enqueue(TEST_USER_ID, n2, initiative_level="medium")
        queue.enqueue(TEST_USER_ID, n3, initiative_level="medium")

        result = queue.dequeue(TEST_USER_ID)
        assert result is not None
        assert result.notification_id == "n3"

    def test_poll_filtering(self, queue: NotificationQueue) -> None:
        now = _utcnow()
        old_n = self._make_notification(
            notif_id="old",
            created_at=(now - timedelta(hours=2)).isoformat(),
        )
        new_n = self._make_notification(
            notif_id="new",
            created_at=(now + timedelta(seconds=10)).isoformat(),
        )
        queue.enqueue(TEST_USER_ID, old_n, initiative_level="medium")
        queue.enqueue(TEST_USER_ID, new_n, initiative_level="medium")

        since = now.isoformat()
        filtered = queue.poll(TEST_USER_ID, since=since)
        ids = [n.notification_id for n in filtered]
        assert "new" in ids
        assert "old" not in ids

    def test_poll_without_since_returns_all(self, queue: NotificationQueue) -> None:
        n1 = self._make_notification(notif_id="n1")
        n2 = self._make_notification(notif_id="n2")
        queue.enqueue(TEST_USER_ID, n1, initiative_level="medium")
        queue.enqueue(TEST_USER_ID, n2, initiative_level="medium")

        all_notifs = queue.poll(TEST_USER_ID)
        assert len(all_notifs) == 2

    def test_pending_count(self, queue: NotificationQueue) -> None:
        n1 = self._make_notification(notif_id="n1")
        n2 = self._make_notification(notif_id="n2")
        n2.read = True
        queue.enqueue(TEST_USER_ID, n1, initiative_level="medium")
        queue.enqueue(TEST_USER_ID, n2, initiative_level="medium")
        assert queue.pending_count(TEST_USER_ID) == 1

    def test_unknown_id_read(self, queue: NotificationQueue) -> None:
        assert queue.mark_read("nonexistent") is False

    def test_unknown_id_dismiss(self, queue: NotificationQueue) -> None:
        assert queue.dismiss("nonexistent") is False

    def test_mark_read_sets_flag(self, queue: NotificationQueue) -> None:
        n = self._make_notification(notif_id="nr")
        queue.enqueue(TEST_USER_ID, n, initiative_level="medium")
        assert queue.mark_read("nr") is True
        assert n.read is True

    def test_dismiss_sets_flag(self, queue: NotificationQueue) -> None:
        n = self._make_notification(notif_id="nd")
        queue.enqueue(TEST_USER_ID, n, initiative_level="medium")
        assert queue.dismiss("nd") is True
        assert n.dismissed is True

    def test_dequeue_empty_queue_returns_none(self, queue: NotificationQueue) -> None:
        assert queue.dequeue(TEST_USER_ID) is None


class TestAgencyAPI:
    @pytest.fixture
    def test_app(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        from chatbot.api.agency import agency_router

        mock_initiative = MagicMock(spec=ProjectInitiative)
        mock_initiative.get_initiative = AsyncMock(return_value=AgencyInitiativeSettings(
            user_id=TEST_USER_ID, level=InitiativeLevel.GENTLE,
        ))
        mock_initiative.set_initiative = AsyncMock()

        mock_scheduler = MagicMock(spec=Scheduler)
        mock_scheduler._events = []
        mock_scheduler.get_next_event = MagicMock(return_value=None)
        mock_scheduler.set_initiative_level = MagicMock()

        mock_notification_queue = MagicMock(spec=NotificationQueue)
        mock_notification_queue.poll = MagicMock(return_value=[])
        mock_notification_queue.pending_count = MagicMock(return_value=0)
        mock_notification_queue.mark_read = MagicMock(return_value=True)
        mock_notification_queue.dismiss = MagicMock(return_value=True)

        mock_checkin_generator = MagicMock(spec=CheckInGenerator)
        mock_checkin_generator.generate = MagicMock(return_value=CheckIn(
            checkin_id="ci-test",
            user_id=TEST_USER_ID,
            checkin_type="reminder",
            message="test check-in",
            context_summary="",
            generated_at=_iso(),
        ))

        app = FastAPI()
        app.include_router(agency_router)

        monkeypatch.setattr("chatbot.api.agency.project_initiative", mock_initiative)
        monkeypatch.setattr("chatbot.api.agency.scheduler", mock_scheduler)
        monkeypatch.setattr("chatbot.api.agency.notification_queue", mock_notification_queue)
        monkeypatch.setattr("chatbot.api.agency.checkin_generator", mock_checkin_generator)

        return TestClient(app)

    def test_get_initiative(self, test_app: TestClient) -> None:
        response = test_app.get(f"/agency/initiative/{TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == TEST_USER_ID
        assert "level" in data

    def test_put_initiative_valid(self, test_app: TestClient) -> None:
        payload = {
            "level": "gentle_checkins",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "08:00",
        }
        response = test_app.put(f"/agency/initiative/{TEST_USER_ID}", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["level"] == "gentle_checkins"
        assert data["quiet_hours_start"] == "22:00"
        assert data["quiet_hours_end"] == "08:00"

    def test_put_initiative_invalid_400(self, test_app: TestClient) -> None:
        payload = {"level": "bogus_level"}
        response = test_app.put(f"/agency/initiative/{TEST_USER_ID}", json=payload)
        assert response.status_code == 400

    def test_get_notifications(self, test_app: TestClient) -> None:
        response = test_app.get(f"/agency/notifications/{TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
        assert "pending_count" in data

    def test_get_notifications_with_since(self, test_app: TestClient) -> None:
        since = _iso()
        response = test_app.get(
            f"/agency/notifications/{TEST_USER_ID}?since={since}"
        )
        assert response.status_code == 200

    def test_mark_read(self, test_app: TestClient) -> None:
        response = test_app.put("/agency/notifications/fake-id/read")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "read"

    def test_dismiss(self, test_app: TestClient) -> None:
        response = test_app.put("/agency/notifications/fake-id/dismiss")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dismissed"

    def test_mark_read_unknown_404(
        self, test_app: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_nq = MagicMock(spec=NotificationQueue)
        mock_nq.mark_read = MagicMock(return_value=False)
        mock_nq.poll = MagicMock(return_value=[])
        mock_nq.pending_count = MagicMock(return_value=0)
        mock_nq.dismiss = MagicMock(return_value=True)
        monkeypatch.setattr("chatbot.api.agency.notification_queue", mock_nq)

        response = test_app.put("/agency/notifications/fake-id-404/read")
        assert response.status_code == 404

    def test_dismiss_unknown_404(
        self, test_app: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_nq = MagicMock(spec=NotificationQueue)
        mock_nq.dismiss = MagicMock(return_value=False)
        mock_nq.poll = MagicMock(return_value=[])
        mock_nq.pending_count = MagicMock(return_value=0)
        mock_nq.mark_read = MagicMock(return_value=True)
        monkeypatch.setattr("chatbot.api.agency.notification_queue", mock_nq)

        response = test_app.put("/agency/notifications/fake-id-404/dismiss")
        assert response.status_code == 404

    def test_trigger_checkin(self, test_app: TestClient) -> None:
        payload = {
            "checkin_type": "reminder",
            "user_id": TEST_USER_ID,
        }
        response = test_app.post("/agency/checkins/trigger", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["checkin_id"] == "ci-test"
        assert "message" in data

    def test_trigger_checkin_invalid_type_400(self, test_app: TestClient) -> None:
        payload = {
            "checkin_type": "invalid_type",
            "user_id": TEST_USER_ID,
        }
        response = test_app.post("/agency/checkins/trigger", json=payload)
        assert response.status_code == 400

    def test_trigger_checkin_unavailable_404(
        self, test_app: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_cg = MagicMock(spec=CheckInGenerator)
        mock_cg.generate = MagicMock(return_value=None)
        monkeypatch.setattr("chatbot.api.agency.checkin_generator", mock_cg)

        payload = {
            "checkin_type": "reminder",
            "user_id": TEST_USER_ID,
        }
        response = test_app.post("/agency/checkins/trigger", json=payload)
        assert response.status_code == 404

    def test_get_schedule(self, test_app: TestClient) -> None:
        response = test_app.get(f"/agency/schedule/{TEST_USER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert "next_event" in data