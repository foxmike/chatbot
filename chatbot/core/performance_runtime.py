"""Turn-local integration for the optional spoken performance protocol."""
import asyncio
import time
from contextlib import suppress
from chatbot.core.performance import DialogueStreamParser, Delta, SegmentComplete, GRACE_SECONDS


def _consume(ctx, parser, events):
    text = []
    for event in events:
        if isinstance(event, Delta):
            text.append(event.text)
        elif isinstance(event, SegmentComplete):
            sink = ctx.metadata.get('_performance_segment_sink')
            if sink:
                sink(event.segment)
    return ''.join(text)


def _diagnostics(ctx, parser, sample):
    from dataclasses import asdict
    supported = set(ctx.metadata.get('performance_caps') or ())
    ignored = sorted({key for segment in parser.segments if segment.performance
                      for key, value in asdict(segment.performance).items() if value and key not in supported})
    diagnostics = parser.diagnostics()
    diagnostics['segments'] = [{**asdict(s), 'text': s.text[:240]} for s in parser.segments[:6]]
    ctx.metadata['performance'] = {**diagnostics, 'unsupported_controls_ignored': ignored, 'raw_sample': sample.encode('utf-8')[:4096].decode('utf-8', errors='ignore'),
                                  'caps': list(ctx.metadata.get('performance_caps') or ())}


def parse_completed_response(ctx, raw):
    parser = DialogueStreamParser()
    text = _consume(ctx, parser, parser.feed(raw) + parser.finish())
    _diagnostics(ctx, parser, raw)
    return text


async def filter_performance_stream(chunks, ctx):
    if not ctx.metadata.get('performance_enabled'):
        try:
            async for chunk in chunks:
                yield chunk
        finally:
            close = getattr(chunks, 'aclose', None)
            if close:
                await close()
        return
    parser = DialogueStreamParser()
    pieces, sample = [], ''
    iterator = chunks.__aiter__()
    pending = None
    try:
        while True:
            _consume(ctx, parser, parser.expire())
            if pending is None:
                pending = asyncio.create_task(anext(iterator))
            since = parser.pending_at if parser.pending_at is not None else parser.line_at
            timeout = max(0, GRACE_SECONDS - (time.monotonic() - since)) if since is not None else None
            ready, _ = await asyncio.wait({pending}, timeout=timeout)
            if not ready:
                _consume(ctx, parser, parser.expire())
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                pending = None
                break
            pending = None
            if chunk.startswith('Provider stream error:'):
                parser.cancel()
                yield chunk
                return
            sample = (sample + chunk)[:4096]
            text = _consume(ctx, parser, parser.feed(chunk))
            if text:
                pieces.append(text)
                yield text
        text = _consume(ctx, parser, parser.finish())
        if text:
            pieces.append(text)
            yield text
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        if not parser.closed:
            parser.cancel()
        close = getattr(iterator, 'aclose', None)
        if close:
            await close()
        if not str(ctx.provider_response or '').startswith('Provider error:'):
            ctx.provider_response = ''.join(pieces)
        _diagnostics(ctx, parser, sample)
