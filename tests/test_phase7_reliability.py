"""Missing PostgreSQL coverage at integrated durable-state boundaries."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
import pytest
import sqlalchemy as sa

from test_completion_persistence import env, USER_ID  # noqa: F401
from test_persistent_identity_integration import add_companion, new_session
from chatbot.db import models
from chatbot.soul.conversational_threads import ThreadObservation, apply_thread_observations, load_open_threads

pytestmark = pytest.mark.skipif(not os.environ.get('PHASE0_PG_URL'), reason='isolated PostgreSQL required')
NOW = datetime.now(timezone.utc)


def observation(status='open'):
    return ThreadObservation(ownership='shared', kind='subject', subject='Review telescope calibration',
                             topic='telescope calibration', status=status, importance='significant',
                             excerpt='Review telescope calibration', role='user')


async def apply(db, cid, sid, job, at, status='open', room_id=None):
    return await apply_thread_observations(db, user_id=USER_ID, companion_id=cid, room_id=room_id,
        observations=[observation(status)], job_id=job, turn_id=job, session_id=str(sid),
        message_id=job, source_at=at)


async def test_concurrent_initial_threads_serialize_and_preserve_both_sources(env):
    cid = await add_companion(env)
    sid = await new_session(env, cid)
    started = asyncio.Event()
    async def second_writer():
        async with env.factory() as db:
            started.set()
            await apply(db, cid, sid, 'second', NOW + timedelta(seconds=1))
            await db.commit()
    async with env.factory() as first:
        await apply(first, cid, sid, 'first', NOW)
        task = asyncio.create_task(second_writer())
        try:
            await started.wait()
            # Uncommitted first writes must serialize a second writer, including
            # an initially empty scope where no thread row exists to lock.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), .15)
            await first.commit()
        finally:
            await first.rollback()
            await task
    async with env.factory() as db:
        rows = list((await db.scalars(sa.select(models.ConversationalThread))).all())
        assert len(rows) == 1
        assert {ref['job_id'] for ref in rows[0].source_evidence} == {'first','second'}


async def test_late_open_cannot_resurrect_a_resolved_thread(env):
    cid = await add_companion(env)
    sid = await new_session(env, cid)
    for job, at, status in [('open', NOW, 'open'), ('resolved', NOW+timedelta(minutes=2), 'resolved'),
                            ('late', NOW+timedelta(minutes=1), 'open')]:
        async with env.factory() as db:
            await apply(db, cid, sid, job, at, status)
            await db.commit()
    async with env.factory() as db:
        assert await load_open_threads(db, user_id=USER_ID, companion_id=cid) == []
        rows = list((await db.scalars(sa.select(models.ConversationalThread))).all())
        assert len(rows) == 1 and rows[0].status == 'resolved'
        assert {ref['job_id'] for ref in rows[0].source_evidence} == {'open','resolved','late'}


async def test_old_resolution_does_not_close_a_more_recent_discussion(env):
    cid = await add_companion(env)
    sid = await new_session(env, cid)
    async with env.factory() as db:
        await apply(db, cid, sid, 'recent', NOW+timedelta(minutes=2))
        await db.commit()
    async with env.factory() as db:
        await apply(db, cid, sid, 'old-resolution', NOW, 'resolved')
        await db.commit()
    async with env.factory() as db:
        rows = await load_open_threads(db, user_id=USER_ID, companion_id=cid)
        assert len(rows) == 1
        assert {ref['job_id'] for ref in rows[0]['source_evidence']} == {'recent','old-resolution'}


async def test_threads_restart_replay_and_real_room_isolation(env):
    cid = await add_companion(env)
    other = await add_companion(env)
    sid = await new_session(env, cid)
    async with env.factory() as db:
        for room in ('room-a','room-b'):
            db.add(models.Room(room_id=room, user_id=str(USER_ID), companion_id=cid,
                display_name=room, room_type='standard', memory_scope='room', persist_to_durable=True))
        await db.commit()
    async with env.factory() as db:
        await apply(db, cid, sid, 'room-job', NOW, room_id='room-a')
        await db.commit()
    # Dispose the entire connection pool; only persisted state may survive.
    await env.engine.dispose()
    async with env.factory() as db:
        await apply(db, cid, sid, 'room-job', NOW, room_id='room-a')
        await db.commit()
    async with env.factory() as db:
        allowed = await load_open_threads(db, user_id=USER_ID, companion_id=cid, room_id='room-a')
        assert len(allowed) == 1 and len(allowed[0]['source_evidence']) == 1
        assert await load_open_threads(db, user_id=USER_ID, companion_id=cid, room_id='room-b') == []
        assert await load_open_threads(db, user_id=USER_ID, companion_id=cid) == []
        assert await load_open_threads(db, user_id=USER_ID, companion_id=other, room_id='room-a') == []
        stored = list((await db.scalars(sa.select(models.ConversationalThread))).all())
        assert len(stored) == 1 and stored[0].room_id == 'room-a'


async def test_all_durable_effects_rollback_then_restore_in_a_fresh_process(env, monkeypatch):
    """Crash after every effect, then replay the receipt and inspect from a new Python process."""
    import json
    import subprocess
    import sys
    from phase2_helpers import extraction_registry
    from test_phase5_integration import _all_categories_response
    from test_persistent_identity_integration import RecordingProvider, standard_turn
    from chatbot.core import turn as turns
    from chatbot.config import Settings
    from chatbot.perf import durable_handlers
    from chatbot.perf.durable_worker import DurableJobWorker

    cid = await add_companion(env)
    sid = await new_session(env, cid)
    reply = 'I value careful repair because it prevents another outage. I was relieved when we fixed it together.'
    provider = RecordingProvider()
    provider.chunks = (reply,)
    monkeypatch.setattr(turns, '_select_provider', lambda _: provider)
    await standard_turn(env, sid, "I live in Seattle. We fixed the outage together after a long night. Let's review the outage report tomorrow.")
    registry, fake = extraction_registry(_all_categories_response())
    original = durable_handlers._apply_emotional_observations
    async def crash_after_all_effects(*args):
        await original(*args)
        raise RuntimeError('injected after all effects')
    monkeypatch.setattr(durable_handlers, '_apply_emotional_observations', crash_after_all_effects)
    def worker():
        handler = durable_handlers.make_durable_memory_extract_handler(
            env.factory, registry=registry, settings=Settings(_env_file=None))
        return DurableJobWorker(env.factory, handlers={'memory_extract':handler},
                                retry_backoff_base_seconds=.001)
    first = worker()
    claimed = await first.claim_due_jobs()
    assert len(claimed) == 1
    await first._run_one(claimed[0])
    assert await env.count(models.SemanticFact) == 0
    assert await env.count(models.MemoryEvidence) == 0
    assert await env.count(models.ConversationalThread) == 0
    async with env.factory() as db:
        identity = await db.get(models.CompanionIdentityModel, cid)
        assert not identity.self_model
        job = (await env.jobs())[0]
        assert job.status == 'pending' and job.payload['extraction_result']
    await env.engine.dispose()
    monkeypatch.setattr(durable_handlers, '_apply_emotional_observations', original)
    restarted = worker()
    claimed = await restarted.claim_due_jobs()
    assert len(claimed) == 1
    await restarted._run_one(claimed[0])
    assert (await env.jobs())[0].status == 'succeeded'
    assert len(fake.calls) == 1  # retry used the durable receipt
    # A duplicate callback after commit is also harmless, without altering the job row.
    await restarted.handlers['memory_extract'](claimed[0])
    assert len(fake.calls) == 1
    target = await new_session(env, cid)
    script = '''
import asyncio, json, os, uuid
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from chatbot.db import models
from chatbot.soul.self_model import load_self_model
from chatbot.soul.conversational_threads import load_open_threads
from chatbot.memory.autobiography import list_experiences
from chatbot.soul.emotional_associations import retrieve_emotional_associations
from chatbot.soul.mood_continuity import load_restorable_mood
async def main():
 engine = create_async_engine(os.environ['PHASE0_PG_URL'])
 factory = async_sessionmaker(engine, expire_on_commit=False)
 uid, cid, sid = uuid.UUID(os.environ['OWNER']), os.environ['COMPANION'], uuid.UUID(os.environ['TARGET'])
 async with factory() as db:
  doc = await load_self_model(db, companion_id=cid, user_id=uid)
  threads = await load_open_threads(db, user_id=uid, companion_id=cid)
  experiences = await list_experiences(db, session_id=sid)
  emotions = await retrieve_emotional_associations(db, session_id=sid, message='outage repair')
  mood = await load_restorable_mood(db, user_id=uid, companion_id=cid, exclude_session_id=sid)
  relationship = await db.get(models.CompanionRelationshipModel, cid)
  companion = await db.get(models.Companion, cid)
  evidence = list((await db.scalars(sa.select(models.MemoryEvidence))).all())
  print(json.dumps({'opinions':len(doc['items']), 'threads':len(threads), 'experiences':len(experiences),
   'emotions':len(emotions), 'mood_source':mood.source_session_id if mood else None,
   'completed_turns':relationship.completed_turns, 'identity_name':companion.name,
   'evidence_ids': sorted(str(e.evidence_id) for e in evidence),
   'evidence_owners':sorted({str(e.user_id) for e in evidence})}))
 await engine.dispose()
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=15,
        env={**os.environ, 'OWNER':str(USER_ID), 'COMPANION':cid, 'TARGET':str(target)})
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot['opinions'] == snapshot['threads'] == snapshot['experiences'] == snapshot['emotions'] == 1
    assert snapshot['mood_source'] == str(sid)
    assert snapshot['completed_turns'] == 1 and snapshot['identity_name'] == 'Mercury'
    assert len(snapshot['evidence_ids']) == len(set(snapshot['evidence_ids'])) == 3
    assert snapshot['evidence_owners'] == [str(USER_ID)]


async def test_thread_update_refreshes_a_previously_loaded_row(env):
    cid = await add_companion(env)
    sid = await new_session(env, cid)
    async with env.factory() as db:
        await apply(db, cid, sid, 'initial', NOW)
        await db.commit()
    async with env.factory() as stale:
        cached = (await stale.scalars(sa.select(models.ConversationalThread))).one()
        async with env.factory() as other:
            await apply(other, cid, sid, 'interleaved', NOW+timedelta(seconds=1))
            await other.commit()
        await apply(stale, cid, sid, 'cached-writer', NOW+timedelta(seconds=2))
        await stale.commit()
        assert cached.thread_id
    async with env.factory() as db:
        row = (await db.scalars(sa.select(models.ConversationalThread))).one()
        assert {ref['job_id'] for ref in row.source_evidence} == {'initial','interleaved','cached-writer'}
