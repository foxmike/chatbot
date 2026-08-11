# CHATBOT_PROMPT_SYSTEM — Model Assignment Reference

## Difficulty Tiers
| Tier | Backend | Model | Count | Tasks |
|------|---------|-------|-------|-------|
| Hardest (complex pipeline wiring) | claude_code_cli | sonnet | 3 | b.5, e.2, f.4 |
| Hard (foundational new modules) | codex_cli | (CLI default) | 7 | b.1, b.2, b.6, c.1, d.8, e.1, g.2 |
| Medium-hard (new modules, moderate) | antigravity_cli | Gemini 3.5 Flash (Medium) | 9 | a.3, a.4, b.3, d.3, d.4, e.4, f.1, g.3 |
| Medium (straightforward modules) | antigravity_cli | Gemini 3.1 Pro (High) | 12 | a.6, a.7, c.2, c.3, c.5, c.6, d.6, d.7, e.3, e.5, f.2, f.3, g.1 |
| Medium-light (config, simple wiring) | pi_cli | deepseek-v4-pro | 10 | a.1, a.2, a.5, b.4, c.4, d.1, d.2, d.5, e.6, f.5 |
| Light (docs) | opencode_cli | gemma-4-31b-it | 2 | g.4, g.5 |

## Task Count by Phase
| Phase | Tasks | Focus |
|-------|-------|-------|
| a | 7 | Memory infrastructure activation |
| b | 6 | Prompt architecture foundation |
| c | 6 | Memory-aware prompting |
| d | 8 | Companion personality enhancement |
| e | 6 | Context & scene intelligence |
| f | 5 | Mode & platform routing |
| g | 5 | Observability & documentation |
| **Total** | **43** | |

## Plan-Patch Commands (only if materializer doesn't honor hints)

Models are already embedded in the skeleton via task_hints. Plan-patch
should only be needed if the materializer overrides or drops hint values.

### Override all codex tasks (if needed to force model):
```bash
P=".abracapocus/plans/ACTIVE/CHATBOT_PROMPT_SYSTEM"

python -m scripts.ops plan-patch --plan-dir "$P" \
  b.5:backend:codex_cli \
  b.6:backend:codex_cli \
  d.8:backend:codex_cli \
  e.2:backend:codex_cli \
  f.4:backend:codex_cli \
  g.2:backend:codex_cli
```

### If you want to swap all antigravity tasks to a different model:
```bash
python -m scripts.ops plan-patch --plan-dir "$P" --phase c \
  --default backend:antigravity_cli \
  --default model:Gemini 3.5 Flash (Medium)
```

## Materialization Command
```bash
export ABRACAPOCUS_MATERIALIZE_BACKEND=pi_cli

python -m scripts.ops plan-materialize \
  --plan-dir .abracapocus/plans/ACTIVE/CHATBOT_PROMPT_SYSTEM
```

## Execution (phase by phase)
```bash
export ABRACAPOCUS_WORKING_ROOT=~/dev/mike/chatbot/python/chatbot
export PROJECT_NAME=chatbot
export ABRACAPOCUS_ALLOW_MAIN=true
export ABRACAPOCUS_BASE_BRANCH=main

python -m scripts.ops phase-run \
  --plan-dir .abracapocus/plans/ACTIVE/CHATBOT_PROMPT_SYSTEM \
  --phase a
```
