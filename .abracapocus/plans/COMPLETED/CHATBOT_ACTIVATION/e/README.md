# e

## Goal

Implement avatar generation and the companion info panel. Wire an image generation provider via OpenRouter in the visual system. Auto-generate avatar on companion creation from name, backstory, and description. Store avatar URL on companion record. Add manual upload alternative. Add Regenerate Avatar button in companion editor. Display avatar next to assistant messages. Create a collapsible right-side companion info panel showing: large avatar, name, description, structured About fields (backstory summary, humor style, communication style, favorite topics), personality trait summary, current relationship status (warmth, directness, initiative), and current mood that updates as conversation progresses. Panel collapses on narrow viewports.

## Notes

- Async: Wave 1: update chatbot/visual/generation.py (OpenRouter image gen), update chatbot/visual/appearance.py (DB-backed storage), create react_ui/src/components/CompanionInfoPanel.tsx. All disjoint. Wave 2: update chatbot/api/visual.py (DB-backed endpoints), update react_ui/src/components/CompanionEditor.tsx (avatar upload + regenerate). Disjoint. Wave 3: update chatbot/api/companions.py (auto-generate avatar on create), update chatbot/companions/companion.py (avatar URL handling). Wave 4: update react_ui/src/components/CompanionSelector.tsx (avatar display in create flow). Wave 5: update react_ui/src/api/client.ts (visual API calls). Wave 6: update react_ui/src/App.tsx (mount CompanionInfoPanel, display avatars next to messages). Wave 6 depends on all prior waves.
- Risk: App.tsx and client.ts are fan-in files modified in phase D; phase ordering serializes. Image generation calls OpenRouter and may be slow; avatar generation should be async and not block companion creation.
