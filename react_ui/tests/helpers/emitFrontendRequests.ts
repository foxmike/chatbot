// Emits, as JSON on stdout, what the real frontend sends for a provider/model
// selection scenario. It drives the real picker logic and the real API client
// (only `fetch` and `localStorage` are stubbed) and builds the avatar socket
// messages with the real protocol builders.
//
// Used by tests/test_provider_routing_integration.py to replay the frontend's
// actual selections against the backend. Run:
//   node --experimental-strip-types tests/helpers/emitFrontendRequests.ts
import {
  applyOllamaDiscovery,
  routingFromStorage,
  selectModel,
  sendBlocker,
  serializeState,
  switchProvider,
  parseStoredState,
  type PickerState,
} from "../../src/utils/modelPicker.ts";
import { buildAuthMessage, buildRoutingMessage } from "../../src/avatar/avatarProtocol.ts";

const OLLAMA_MODELS = [
  "gemma4:12b",
  "hf.co/KevinJK51/Qwen3.6-12B-IQ-Ultra-Heretic-Uncensored-Thinking-V2-Hightop-GGUF:Q4_K_M",
];
const OLLAMA_PICK = OLLAMA_MODELS[1];

(globalThis as { localStorage?: unknown }).localStorage = {
  getItem: () => null,
  setItem: () => undefined,
  removeItem: () => undefined,
};

interface Captured {
  path: string;
  body: Record<string, unknown>;
}
let captured: Captured[] = [];
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = String(input).replace(/^https?:\/\/[^/]+/, "").replace(/^\/api/, "");
  captured.push({ path, body: JSON.parse(String(init?.body ?? "{}")) });
  if (path.startsWith("/chat/stream")) {
    return new Response("data: [DONE]\n\n", {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }
  return new Response(JSON.stringify({ session_id: "s", response: "ok", turn_id: "t" }), {
    status: 200,
  });
}) as typeof fetch;

const client = await import("../../src/api/client.ts");

const SESSION = "00000000-0000-0000-0000-000000000001";

async function send(state: PickerState, streaming: boolean): Promise<Captured | null> {
  // ChatInput refuses to send while the picker reports a blocker.
  if (sendBlocker(state)) return null;
  captured = [];
  if (streaming) {
    await client.streamChat(SESSION, "hello there", {}, undefined, undefined, state.provider, state.model);
  } else {
    await client.sendMessage("hello there", SESSION, undefined, state.provider, state.model);
  }
  return captured[0] ?? null;
}

function avatarMessages(state: PickerState) {
  // The popup reads the picker's persisted state, not the main window's memory.
  const routing = routingFromStorage(serializeState(state));
  return {
    auth: buildAuthMessage(
      { token: "t", sessionId: SESSION, companionId: "c", roomId: null, tabId: "tab" },
      routing,
    ),
    routing: buildRoutingMessage(routing),
  };
}

const output: unknown[] = [];
for (const streaming of [false, true]) {
  let state = parseStoredState(null);
  const record = async (step: string) =>
    output.push({
      streaming,
      step,
      picker: { provider: state.provider, model: state.model },
      http: await send(state, streaming),
      blocker: sendBlocker(state),
      avatar: avatarMessages(state),
    });

  await record("openrouter-initial");

  state = applyOllamaDiscovery(switchProvider(state, "ollama"), { ok: true, models: OLLAMA_MODELS });
  state = selectModel(state, OLLAMA_PICK);
  await record("ollama-selected");

  state = switchProvider(state, "openrouter");
  await record("back-to-openrouter");

  state = applyOllamaDiscovery(switchProvider(state, "ollama"), {
    ok: false,
    message: "Cannot connect to Ollama at http://localhost:11434/v1: ConnectError",
  });
  await record("ollama-unavailable");
}

console.log(JSON.stringify(output));
