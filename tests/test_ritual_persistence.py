"""Ritual transaction ownership with real SQLAlchemy sessions on a temporary DB.

Uses the explicit SQLite BEGIN recipe from test_completion_persistence so
rollback behavior is real. No configured application database is accessed.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chatbot.continuity import rituals as ritual_module
from chatbot.continuity.rituals import CompanionRitualModel, Rituals
from chatbot.db import models

USER_ID = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
SESSION_ID = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')


@pytest.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rituals.db'}")

    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _record):
        connection.isolation_level = None

    @event.listens_for(engine.sync_engine, 'begin')
    def begin(connection):
        connection.exec_driver_sql('BEGIN')

    async with engine.begin() as connection:
        for model in (models.User, models.Session, CompanionRitualModel):
            await connection.run_sync(lambda sync, m=model: m.__table__.create(sync))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(models.User(id=USER_ID, external_id='ritual-test', username='ritual-test',
                           email='ritual@example.com', display_name='R', password_hash='h'))
        await db.flush()
        db.add(models.Session(id=SESSION_ID, user_id=USER_ID, title='Original'))
        await db.commit()
    try:
        yield factory, Rituals(factory)
    finally:
        await engine.dispose()


async def create(rituals, **kwargs):
    return await rituals.create_ritual(
        str(SESSION_ID), 'Daily', 'check_in', 'Check in', 'daily',
        'How are you?', metadata={'shared': 'routine'}, **kwargs,
    )


async def load(factory, ritual_id):
    async with factory() as db:
        return await db.get(CompanionRitualModel, uuid.UUID(ritual_id))


async def test_factory_creation_survives_session_close(store):
    factory, rituals = store
    ritual = await create(rituals)
    row = await load(factory, ritual.ritual_id)
    assert row is not None
    assert row.session_id == SESSION_ID
    assert row.name == 'Daily'
    assert row.metadata_json == {'shared': 'routine'}
    assert row.trigger_prompt == 'How are you?'
    assert row.last_triggered_at is None
    assert row.next_trigger_at is None
    assert row.is_active
    assert (await rituals.get_rituals(str(SESSION_ID)))[0] == ritual


async def test_trigger_updates_and_deactivation_persist_across_sessions(store):
    factory, rituals = store
    ritual = await create(rituals)
    assert await rituals.trigger_ritual(ritual.ritual_id) == 'How are you?'
    row = await load(factory, ritual.ritual_id)
    assert row.last_triggered_at is not None
    assert row.next_trigger_at - row.last_triggered_at == timedelta(days=1)
    assert await rituals.get_due_rituals(str(SESSION_ID)) == []
    await rituals.deactivate_ritual(ritual.ritual_id)
    row = await load(factory, ritual.ritual_id)
    assert not row.is_active
    assert await rituals.get_rituals(str(SESSION_ID)) == []
    assert len(await rituals.get_rituals(str(SESSION_ID), active_only=False)) == 1


async def test_inside_joke_persists_with_existing_metadata(store):
    factory, rituals = store
    ritual = await rituals.add_inside_joke(str(SESSION_ID), 'Cells', 'Again?', 'Spreadsheet')
    row = await load(factory, ritual.ritual_id)
    assert row.ritual_type == 'joke'
    assert row.metadata_json == {'context': 'Spreadsheet', 'joke_text': 'Again?'}
    assert row.description == 'Spreadsheet'
    assert row.trigger_prompt == 'Again?'


@pytest.mark.parametrize('operation', ['create', 'joke', 'trigger', 'deactivate'])
async def test_caller_owns_commit_and_rollback(store, monkeypatch, operation):
    factory, rituals = store
    monkeypatch.setattr(ritual_module, '_default_rituals', rituals)
    existing = await create(rituals)
    async with factory() as db:
        def unexpected_commit(_session):
            pytest.fail('Ritual operation committed a caller-owned transaction')
        event.listen(db.sync_session, 'before_commit', unexpected_commit)
        owner = await db.get(models.Session, SESSION_ID)
        owner.title = 'Caller change'
        if operation == 'create':
            await ritual_module.create_ritual(
                str(SESSION_ID), 'Borrowed', 'custom', 'Description', 'weekly', session=db,
            )
        elif operation == 'joke':
            await ritual_module.add_inside_joke(
                str(SESSION_ID), 'Borrowed', 'Joke', 'Context', session=db,
            )
        elif operation == 'trigger':
            assert await ritual_module.trigger_ritual(existing.ritual_id, session=db) == 'How are you?'
        else:
            await ritual_module.deactivate_ritual(existing.ritual_id, session=db)
        # The supplied session is still open, and changes were flushed.
        rows = (await db.execute(select(CompanionRitualModel))).scalars().all()
        assert len(rows) == (2 if operation in ('create', 'joke') else 1)
        if operation == 'trigger':
            assert rows[0].last_triggered_at is not None
        if operation == 'deactivate':
            assert not rows[0].is_active
        await db.rollback()
        event.remove(db.sync_session, 'before_commit', unexpected_commit)
    async with factory() as db:
        assert (await db.get(models.Session, SESSION_ID)).title == 'Original'
        rows = (await db.execute(select(CompanionRitualModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].is_active
        assert rows[0].last_triggered_at is None
    # Caller may also choose to commit successfully.
    async with factory() as db:
        created = await create(rituals, session=db)
        await db.commit()
    assert await load(factory, created.ritual_id) is not None


@pytest.mark.parametrize("operation", ["create", "trigger", "deactivate"])
async def test_failed_owned_write_rolls_back_flushed_changes(store, operation):
    factory, rituals = store
    existing = await create(rituals)
    failure = RuntimeError('commit failed after flush')

    def failing_factory():
        db = factory()
        def fail(session):
            session.flush()
            raise failure
        event.listen(db.sync_session, 'before_commit', fail)
        return db

    with pytest.raises(RuntimeError) as caught:
        failing = Rituals(failing_factory)
        if operation == "create":
            await create(failing)
        elif operation == "trigger":
            await failing.trigger_ritual(existing.ritual_id)
        else:
            await failing.deactivate_ritual(existing.ritual_id)
    assert caught.value is failure
    async with factory() as db:
        rows = (await db.execute(select(CompanionRitualModel))).scalars().all()
        assert len(rows) == 1
        assert str(rows[0].id) == existing.ritual_id
        assert rows[0].is_active
        assert rows[0].last_triggered_at is None
        assert rows[0].next_trigger_at is None
    # A subsequent owned write still works.
    await create(Rituals(factory))


async def test_reads_never_commit_and_missing_writes_keep_value_error(store):
    factory, rituals = store
    ritual = await create(rituals)

    def read_factory():
        db = factory()
        def unexpected_commit(_session):
            pytest.fail('Read-only operation committed')
        event.listen(db.sync_session, 'before_commit', unexpected_commit)
        return db

    reader = Rituals(read_factory)
    assert len(await reader.get_rituals(str(SESSION_ID))) == 1
    assert await reader.get_due_rituals(str(SESSION_ID)) == []
    for method in (rituals.trigger_ritual, rituals.deactivate_ritual):
        with pytest.raises(ValueError, match='not found'):
            await method(str(uuid.uuid4()))
    assert (await load(factory, ritual.ritual_id)).is_active
