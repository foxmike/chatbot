# d

## Goal

Latency and scale. Token streaming with typing indicators and partial generation on the /chat/stream path. Background async processing moving memory extraction and consolidation off the response path so replies return fast. Cached persona context to avoid recompiling persona/prompt every turn. Short-response mode. Targets the cold-start and on-path-work latency observed in early testing.

## Notes

- Async: Wave 1 (disjoint): perf/__init__.py + cache.py as one package task, background.py (async task queue), api/streaming.py token streaming and typing indicators, and client.ts SSE consumption. Wave 2: chatbot/core/turn.py splitting fast response path from deferred background steps (single-owner, depends on cache.py and background.py), consolidation/worker.py consuming the background queue (depends on background.py), and react MessageList streaming render and typing indicator (depends on client.ts). Wave 3: chatbot/api/chat.py enqueuing background work instead of running it inline (single-owner, depends on the turn.py task and background.py). Test fan-in last.
- Risk: turn.py and chat.py are core and single-owner; the fast/deferred split must preserve correctness — a reply must never depend on work that was deferred. Background processing topology (in-process Redis worker vs separate process) follows the operator answer and must match the VPS deployment. cache.py invalidation must not serve stale persona after a character/relationship change from phase L. The streaming change must remain compatible with the existing /chat/stream contract.
