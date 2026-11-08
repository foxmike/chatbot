"""Focused expression, protocol and real conversation lifecycle acceptance."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_completion_persistence import env  # noqa: F401
from test_persistent_identity_integration import RecordingProvider, add_companion, new_session, standard_turn, stream_turn, spoken_turn
from chatbot.core.steps import TurnContext
from chatbot.core import turn as turns
from chatbot.api import streaming
from chatbot.core.performance import (DialogueStreamParser, Performance, PerformanceCapabilities,
    Delta, SegmentComplete, parse_dialogue, performance_prompt_block)
from chatbot.core.performance_runtime import filter_performance_stream
from chatbot.soul.expression_context import expression_context
from chatbot.avatar.performance_mapping import SpeechDeliveryMapper, NullMapper, map_delivery

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/phase6_behavioral_acceptance.json').read_text())
RAW = 'That worked!\n@@ {"emo":["relieved","hesitant"],"int":"mid","pause":"long"}\n\nStill, let us check.\n@@ {"tone":"dry","emph":["check"]}'
CLEAN = 'That worked!\n\nStill, let us check.'


def context(persona=None, **metadata):
    return TurnContext('phase6', 'Can you explain this carefully?', [], persona or {}, [], None, None, [], None, metadata)


@pytest.mark.parametrize('scenario', FIXTURE['scenarios'], ids=lambda s:s['scenario_id'])
def test_fixture_context_contract(scenario):
    configs = scenario.get('companion_personality_configurations') or [scenario['companion_personality_configuration']]
    texts = []
    for config in configs:
        linguistic = config.get('linguistic_fingerprint') or {}
        persona = {'description': config.get('persona'), 'tone': config.get('voice_and_tone'),
                   'personality_sliders': config.get('personality_anchors') or {}, **linguistic,
                   'natural_imperfections': linguistic.get('speech_quirk') or linguistic.get('stylistic_quirks')}
        ctx = context(persona)
        ctx.user_message = scenario['user_conversation_sequence'][0]['content']
        before = json.dumps(persona, sort_keys=True)
        text, diagnostic = expression_context(ctx)
        assert len(text) <= 2100
        assert diagnostic['fingerprint']['characteristics']
        assert diagnostic['rhythm']
        assert 'never' in text.lower() or 'not' in text.lower()
        assert json.dumps(persona, sort_keys=True) == before
        other = context(persona)
        sequence = scenario['user_conversation_sequence']
        other.user_message = sequence[-1]['content'] if len(sequence) > 1 else 'Hello, how was your day?'
        _, changed = expression_context(other)
        assert changed['fingerprint']['characteristics'] == diagnostic['fingerprint']['characteristics']
        if 'scenario_3_' in scenario['scenario_id']:
            assert 'do not force humor' in text
        if 'scenario_4_' in scenario['scenario_id']:
            assert 'natural_imperfections' in diagnostic['fingerprint']['characteristics']
        if 'scenario_6_' in scenario['scenario_id']:
            assert diagnostic['suppresses_topic_steering'] is True
        texts.append(text)
    if len(configs) > 1:
        assert len(set(texts)) == len(configs)


def test_all_chunk_boundaries_and_paragraphs():
    expected = parse_dialogue(RAW)
    assert expected.text == CLEAN
    assert expected.segments[0].performance.emotion == ('relieved','hesitant')
    for split in range(len(RAW)+1):
        parser = DialogueStreamParser()
        events = parser.feed(RAW[:split]) + parser.feed(RAW[split:]) + parser.finish()
        assert ''.join(e.text for e in events if isinstance(e, Delta)) == CLEAN
        assert tuple(parser.segments) == expected.segments
    parser = DialogueStreamParser()
    events = []
    for char in RAW:
        events.extend(parser.feed(char))
    events.extend(parser.finish())
    assert ''.join(e.text for e in events if isinstance(e, Delta)) == CLEAN


@pytest.mark.parametrize('metadata,reason', [('{"tone":', 'bad_json'), ('{oops}', 'bad_json'),
    ('{"pace":"impossible","emph":["missing"]}', 'bad_pace'), ('[]', 'not_object'),
    ('{"tone":"' + 'x'*300 + '"}', 'too_long')])
def test_malformed_and_partial_metadata_keeps_dialogue(metadata, reason):
    parser = DialogueStreamParser()
    events = parser.feed('Keep this.\n@@ ' + metadata) + parser.finish()
    assert ''.join(e.text for e in events if isinstance(e, Delta)) == 'Keep this.'
    assert parser.dropped[reason]


@pytest.mark.parametrize('text', ['Contact @@ name.', '@@ is an example', '"@@ {not protocol}"', 'Use `@@ {}` literally.', '@', '@@'])
def test_literals_remain_visible(text):
    assert parse_dialogue(text).text == text


def test_budget_grace_and_cancellation():
    parser = DialogueStreamParser()
    events = parser.feed('Hello.\n')
    assert ''.join(e.text for e in events if isinstance(e, Delta)) == 'Hello.'
    assert parser.expire(parser.pending_at + .301)[0].segment.performance is None
    parser.feed('@@ {"pause":"long"}\n')
    assert parser.dropped['late'] == 1
    parser = DialogueStreamParser()
    parser.feed(('A.\n@@ {"pause":"long"}\n') * 8)
    parser.finish()
    assert parser.dropped['over_limit'] == 2
    assert len(parser.probe) <= 240
    parser = DialogueStreamParser()
    parser.feed('Already visible\n@@ {"tone":')
    parser.cancel()
    assert parser.closed and parser.pending is None and not parser.probe
    assert parser.finish() == []


def test_capabilities_and_tts_fallback():
    performance = Performance(emotion=('sad','relieved'), pace='slow', pause='long')
    controls, ignored = map_delivery(performance, SpeechDeliveryMapper())
    assert controls.pause_seconds == .4
    assert set(ignored) == {'emotion','pace'}
    assert map_delivery(performance, NullMapper())[0].pause_seconds is None
    class Broken(NullMapper):
        def map(self, value):
            raise RuntimeError('unavailable')
    assert 'mapper_error' in map_delivery(performance, Broken())[1]
    prompt = performance_prompt_block(PerformanceCapabilities(('pause',)))
    assert len(prompt) <= 900 and 'Use only these optional keys: pause.' in prompt
    assert performance_prompt_block(PerformanceCapabilities()) == ''


@pytest.mark.asyncio
async def test_stream_first_text_no_markup_and_disabled_identity():
    ctx = context(performance_enabled=True, performance_caps=('pause',))
    delivered = []
    ctx.metadata['_performance_segment_sink'] = delivered.append
    async def chunks():
        for char in RAW:
            yield char
    stream = filter_performance_stream(chunks(), ctx)
    assert await anext(stream) == 'T'
    rest = [chunk async for chunk in stream]
    assert 'T' + ''.join(rest) == CLEAN == ctx.provider_response
    assert len(delivered) == 2
    assert 'emotion' in ctx.metadata['performance']['unsupported_controls_ignored']
    ctx = context()
    original = [chunk async for chunk in filter_performance_stream(chunks(), ctx)]
    assert ''.join(original) == RAW


@pytest.mark.asyncio
async def test_cancelled_stream_has_clean_partial_response():
    ctx = context(performance_enabled=True)
    waiting = asyncio.Event()
    async def chunks():
        yield 'Delivered\n@@ {"tone":'
        waiting.set()
        await asyncio.Event().wait()
    stream = filter_performance_stream(chunks(), ctx)
    assert await anext(stream) == 'Delivered'
    task = asyncio.create_task(anext(stream))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ctx.provider_response == 'Delivered'
    assert ctx.metadata['performance']['dropped']['cancelled_metadata'] == 1


@pytest.mark.asyncio
async def test_provider_stream_and_completion_share_clean_canonical_response(monkeypatch):
    class Provider:
        calls = 0
        async def stream(self, **kw):
            self.calls += 1
            for char in RAW:
                yield char
        async def complete(self, **kw):
            self.calls += 1
            return SimpleNamespace(text=RAW)
    provider = Provider()
    monkeypatch.setattr(turns, '_select_provider', lambda _: provider)
    for mode in ('standard','sse','spoken'):
        ctx = context(performance_enabled=True, performance_caps=('pause',))
        if mode == 'standard':
            await turns.CallProviderStep().run(ctx)
        else:
            assert ''.join([s async for s in streaming._stream_provider_tokens(ctx)]) == CLEAN
        assert ctx.provider_response == CLEAN
        await turns.RespondStep().run(ctx)
        assert ctx.final_response == CLEAN
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_shared_modes_and_clean_persistence(env, monkeypatch):
    provider = RecordingProvider()
    monkeypatch.setattr(turns, '_select_provider', lambda _: provider)
    inline = AsyncMock(side_effect=AssertionError('no inline extraction'))
    monkeypatch.setattr(turns.MemoryExtractor, 'extract', inline)
    cid = await add_companion(env, name='Distinctive archivist')
    sessions = [await new_session(env, cid) for _ in range(3)]
    await standard_turn(env, sessions[0])
    await stream_turn(env, sessions[1])
    await spoken_turn(env, sessions[2], cid, provider, monkeypatch)
    assert len(provider.inputs) == 3
    systems = [item['history'][0]['content'] for item in provider.inputs]
    for system in systems:
        assert 'Configured voice anchors' in system
        assert 'Natural options for your reply' in system
        assert 'Voice' in system
        assert '@@' not in system
    # Working mood and session history may differ; stable voice is shared.
    anchors = [s.split('Configured voice anchors:')[1].split('Let them shape')[0] for s in systems]
    assert len(set(anchors)) == 1
    inline.assert_not_awaited()
    for message in await env.messages():
        assert '@@' not in message.content


def test_parser_overhead(capsys):
    start = perf_counter()
    parser = DialogueStreamParser()
    first = parser.feed('T')
    first_ms = (perf_counter()-start)*1000
    parser.feed(RAW[1:]); parser.finish()
    elapsed = (perf_counter()-start)*1000
    assert first[0].text == 'T'
    print(f'phase6_parser_first_text_ms={first_ms:.3f} total_ms={elapsed:.3f} metadata_chars={len(RAW)-len(CLEAN)}')


@pytest.mark.asyncio
async def test_enabled_spoken_persists_clean_dialogue_and_job(env, monkeypatch):
    from chatbot.avatar import router
    from chatbot.avatar.coordinator import AvatarCoordinator, AvatarBinding
    from chatbot.config import get_settings
    from test_avatar_backend import FakeRuntime, FakeSocket
    from test_completion_persistence import USER, USER_ID, app_with_engine
    class PlayedRuntime(FakeRuntime):
        async def feed_text(self, turn_id, text):
            await super().feed_text(turn_id, text)
            turn = self.binding.turn
            turn.acknowledged.add(f'{turn_id}:{self.n}')
            turn.playback_changed.set()
    provider = RecordingProvider()
    provider.chunks = tuple(RAW)
    monkeypatch.setattr(turns, '_select_provider', lambda _: provider)
    monkeypatch.setattr(router, 'async_session_factory', env.factory)
    monkeypatch.setattr(get_settings(), 'avatar_performance_debug', True)
    cid = await add_companion(env)
    sid = await new_session(env, cid)
    hub = AvatarCoordinator()
    binding = AvatarBinding(str(sid), str(USER_ID), cid, FakeSocket())
    await hub.connect(binding, PlayedRuntime)
    app = app_with_engine()
    app.state.avatar_coordinator = hub
    ws = SimpleNamespace(scope={'type':'websocket','app':app,'headers':[], 'path':'/api/avatar/ws','query_string':b''})
    try:
        await asyncio.wait_for(router._spoken_turn(ws, binding, USER, 'Can you explain the repair?'), 5)
        assert len(provider.inputs) == 1
        assert '@@' in provider.system()
        assert ' '.join(''.join(binding.runtime.tokens).split()) == ' '.join(CLEAN.split())
        messages = await env.messages()
        assistant = [m for m in messages if m.role == 'assistant']
        assert len(assistant) == 1 and assistant[0].content == CLEAN
        jobs = await env.jobs()
        assert len(jobs) == 1
        assert '@@' not in str(jobs[0].payload)
        assert all('@@' not in m.content for m in messages)
    finally:
        await hub.detach(binding)


@pytest.mark.asyncio
async def test_pause_segment_boundary_uses_existing_chunker():
    from chatbot.avatar.media import AvatarMediaEngine, _Turn
    engine = object.__new__(AvatarMediaEngine)
    turn = _Turn('test', 1, None, {'chunks':0, 'chunk_chars':0, 'sentence_cuts':0, 'mid_sentence_cuts':0})
    turn.voice = {'voice_id':'unchanged-clone'}
    engine._turns = {'test':turn}
    engine.turn = turn
    engine.mark = lambda *a, **kw: None
    engine._diagnostic = AsyncMock()
    await engine.feed_performance_segment('test', 'First sentence.', .4)
    await engine.feed_performance_segment('test', 'Second sentence.', None)
    first = await engine._next_chunk(turn, 0)
    second = await engine._next_chunk(turn, 1)
    assert first.text.strip() == 'First sentence.' and first.pause_seconds == .4
    assert second.text.strip() == 'Second sentence.' and second.pause_seconds is None
    assert turn.voice['voice_id'] == 'unchanged-clone'


@pytest.mark.asyncio
async def test_shared_compile_does_not_call_embedding_or_model(monkeypatch):
    embed, provider = AsyncMock(), AsyncMock()
    monkeypatch.setattr(turns, 'generate_embedding', embed)
    ctx = context({'description':'Dry, measured and precise.', 'humor_style':'dry'},
                  provider=provider, companion_self_model={'items':{}}, phase5_roleplay_context=True)
    step = turns.CompileContextStep()
    await step.run(ctx)
    embed.assert_not_awaited()
    provider.assert_not_awaited()
    assert 'Configured voice anchors' in ctx.metadata['persona_prompt']
    assert 'expression' in ctx.metadata['identity_sources']


@pytest.mark.asyncio
async def test_grace_releases_speech_while_provider_is_still_waiting():
    ctx = context(performance_enabled=True)
    released = asyncio.Event()
    segments = []
    def sink(segment):
        segments.append(segment)
        released.set()
    ctx.metadata['_performance_segment_sink'] = sink
    async def chunks():
        yield 'First sentence.\n'
        await asyncio.Event().wait()
    stream = filter_performance_stream(chunks(), ctx)
    assert await anext(stream) == 'First sentence.'
    waiting = asyncio.create_task(anext(stream))
    await asyncio.wait_for(released.wait(), .6)
    assert segments[0].text == 'First sentence.' and segments[0].performance is None
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert ctx.provider_response == 'First sentence.'


def test_field_validation_and_segment_bound_preserve_real_dialogue():
    from chatbot.core.performance import validate_performance, MAX_SEGMENTS
    performance, errors = validate_performance({
        'emo':['sad','relieved','extra'], 'int':'high', 'tone':7, 'style':'x'*41,
        'pace':'fast', 'emph':['he','check'], 'unsupported':'value',
    }, 'the check')
    assert performance.emotion == ('sad','relieved')
    assert performance.intensity == 'high' and performance.pace == 'fast'
    assert performance.tone is None and performance.style is None
    assert performance.emphasis == ('check',)
    assert {'bad_tone','bad_style','bad_emph','unknown_field','over_emo'} <= set(errors)
    raw = '\n'.join('Line.' for _ in range(MAX_SEGMENTS + 2))
    parser = DialogueStreamParser()
    events = parser.feed(raw) + parser.finish()
    assert ''.join(e.text for e in events if isinstance(e, Delta)) == raw
    assert len(parser.segments) == MAX_SEGMENTS
    assert parser.dropped['segment_limit'] == 2
    text, _ = expression_context(context({'persona':'A sardonic desert historian.'}))
    assert 'sardonic desert historian' in text
