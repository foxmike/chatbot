# a

## Goal

Self-contained React chat UI in react_ui/, built with Vite + TypeScript + Tailwind, clean minimal dark theme. Connects to the existing FastAPI endpoints (POST /sessions/, POST /chat/, /chat/stream) with no backend changes. Component structure is designed to absorb later features (voice, avatars, mood indicators, memory controls, room switching) without rework. Vite dev server for local development; a production nginx Docker image wired into docker-compose.yml so VPS deployment is `docker compose up` with no Node runtime in production.

## Notes

- Async: Wave 1: the project scaffold (package.json, vite.config.ts, tsconfig.json, tailwind.config.js, postcss.config.js, index.html, src/main.tsx, src/index.css) as one task, and src/types.ts as a second disjoint task. Wave 2 runs three parallel tasks on disjoint files, each depending only on types.ts: the API client (src/api/client.ts), the message display components (src/components/ChatMessage.tsx + src/components/MessageList.tsx), and the input plus session sidebar (src/components/ChatInput.tsx + src/components/SessionSidebar.tsx). Wave 3 has two parallel tasks: App.tsx (fan-in wiring the client and components), and the production packaging (react_ui/Dockerfile + react_ui/nginx.conf + docker-compose.yml ui service). docker-compose.yml is owned only by the packaging task so no other task contends for it.
- Risk: Frontend-only; no Python or FastAPI changes. Keep react_ui fully self-contained. The only cross-cutting file is docker-compose.yml; isolate it to the single packaging task to avoid write conflicts with the running stack. The UI adapts to the existing API response shapes (session_id, response, step_results); do not alter endpoints.
