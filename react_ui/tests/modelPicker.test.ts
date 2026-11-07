import test from "node:test";
import assert from "node:assert/strict";
import {
  BUILT_IN_MODELS,
  OLLAMA_EMPTY_MESSAGE,
  OLLAMA_LOADING_MESSAGE,
  STORAGE_KEY,
  addModel,
  applyOllamaDiscovery,
  loadPickerState,
  ollamaConnectionMessage,
  parseStoredState,
  removeModel,
  routingFromStorage,
  routingSelection,
  selectModel,
  sendBlocker,
  serializeState,
  switchProvider,
  type PickerState,
} from "../src/utils/modelPicker.ts";
import { buildAuthMessage, buildRoutingMessage } from "../src/avatar/avatarProtocol.ts";

const HF_MODEL =
  "hf.co/KevinJK51/Qwen3.6-12B-IQ-Ultra-Heretic-Uncensored-Thinking-V2-Hightop-GGUF:Q4_K_M";
const UNREACHABLE = "Cannot connect to Ollama at http://localhost:11434/v1: ConnectError";

function fresh(): PickerState {
  return parseStoredState(null);
}

/** Select Ollama and complete discovery, the way the component does. */
function onOllama(state: PickerState, models: string[]): PickerState {
  return applyOllamaDiscovery(switchProvider(state, "ollama"), { ok: true, models });
}

// ---------------------------------------------------------------- Ollama discovery

test("Ollama returns zero models: shows empty state and blocks sending", () => {
  const selecting = switchProvider(fresh(), "ollama");
  // While discovery is in flight nothing may be sent.
  assert.equal(sendBlocker(selecting), OLLAMA_LOADING_MESSAGE);

  const state = applyOllamaDiscovery(selecting, { ok: true, models: [] });

  assert.equal(state.provider, "ollama");
  assert.deepEqual(state.models.ollama, []);
  assert.equal(state.model, "");
  assert.deepEqual(state.ollamaStatus, { kind: "empty" });
  assert.equal(OLLAMA_EMPTY_MESSAGE, "No Ollama models available");
  assert.equal(sendBlocker(state), "No Ollama models available");
});

test("Ollama returns one model: it is selected automatically with the exact ID", () => {
  const state = onOllama(fresh(), [HF_MODEL]);

  assert.deepEqual(state.models.ollama, [HF_MODEL]);
  assert.equal(state.model, HF_MODEL);
  assert.equal(state.ollamaModel, HF_MODEL);
  assert.equal(sendBlocker(state), null);
});

test("Ollama returns multiple models: all listed in order, first selected, user can choose", () => {
  const models = ["llama3.2:latest", HF_MODEL, "qwen3:8b"];
  let state = onOllama(fresh(), models);

  assert.deepEqual(state.models.ollama, models);
  assert.equal(state.model, "llama3.2:latest");
  assert.equal(sendBlocker(state), null);

  state = selectModel(state, HF_MODEL);
  assert.equal(state.model, HF_MODEL);
  assert.equal(state.ollamaModel, HF_MODEL);
  assert.equal(sendBlocker(state), null);
});

test("Ollama unreachable: clear error, no models, sending blocked, no provider fallback", () => {
  const state = applyOllamaDiscovery(switchProvider(fresh(), "ollama"), {
    ok: false,
    message: UNREACHABLE,
  });

  assert.equal(state.provider, "ollama"); // never silently switched to OpenRouter
  assert.deepEqual(state.models.ollama, []);
  assert.equal(state.model, "");
  assert.deepEqual(state.ollamaStatus, { kind: "error", message: UNREACHABLE });
  assert.equal(sendBlocker(state), UNREACHABLE);
  assert.notEqual(state.provider, "openrouter");
});

test("connection errors are turned into a clear message", () => {
  assert.equal(
    ollamaConnectionMessage({ detail: UNREACHABLE, status_code: 502 }),
    UNREACHABLE,
  );
  assert.equal(
    ollamaConnectionMessage(new TypeError("Failed to fetch")),
    "Cannot connect to Ollama: Failed to fetch",
  );
  assert.equal(ollamaConnectionMessage(undefined), "Cannot connect to Ollama");
});

test("a successful refresh clears an earlier error", () => {
  let state = applyOllamaDiscovery(switchProvider(fresh(), "ollama"), {
    ok: false,
    message: UNREACHABLE,
  });
  state = onOllama(switchProvider(state, "openrouter"), ["llama3.2:latest"]);

  assert.deepEqual(state.ollamaStatus, { kind: "ready" });
  assert.equal(sendBlocker(state), null);
});

test("Ollama model IDs are used exactly as returned (no trimming, case or tag changes)", () => {
  const exact = ["Mixed-Case/Model:Q4_K_M", "model:latest", "hf.co/x/y:IQ4_XS"];
  const state = onOllama(fresh(), exact);

  assert.deepEqual(state.models.ollama, exact);
});

test("Ollama has no add-model or remove-model behavior", () => {
  const state = onOllama(fresh(), ["a:1", "b:2"]);

  assert.equal(addModel(state, "typed:model"), state);
  assert.equal(removeModel(state, "a:1"), state);
});

// ---------------------------------------------------------------- Switching providers

test("switching Ollama -> OpenRouter -> Ollama refreshes and keeps the model if still installed", () => {
  const models = ["llama3.2:latest", HF_MODEL];
  let state = onOllama(fresh(), models);
  state = selectModel(state, HF_MODEL);

  state = switchProvider(state, "openrouter");
  assert.equal(state.provider, "openrouter");
  assert.equal(state.model, BUILT_IN_MODELS.openrouter[0]);
  assert.equal(sendBlocker(state), null);

  // Selecting Ollama again starts a fresh discovery: nothing selectable until it lands.
  state = switchProvider(state, "ollama");
  assert.deepEqual(state.ollamaStatus, { kind: "loading" });
  assert.deepEqual(state.models.ollama, []);
  assert.equal(sendBlocker(state), OLLAMA_LOADING_MESSAGE);

  state = applyOllamaDiscovery(state, { ok: true, models });
  assert.equal(state.model, HF_MODEL);
  assert.deepEqual(state.models.ollama, models);
  assert.equal(sendBlocker(state), null);
});

test("previously selected Ollama model still available: it stays selected after refresh", () => {
  let state = onOllama(fresh(), ["a:1", "b:2", "c:3"]);
  state = selectModel(state, "b:2");

  state = applyOllamaDiscovery(switchProvider(state, "ollama"), {
    ok: true,
    models: ["c:3", "b:2", "a:1"],
  });

  assert.equal(state.model, "b:2");
});

test("previously selected Ollama model removed: first available model is selected", () => {
  let state = onOllama(fresh(), ["a:1", "b:2", "c:3"]);
  state = selectModel(state, "b:2");

  state = applyOllamaDiscovery(switchProvider(switchProvider(state, "openrouter"), "ollama"), {
    ok: true,
    models: ["c:3", "a:1"],
  });

  assert.equal(state.model, "c:3");
  assert.equal(state.ollamaModel, "c:3");
  assert.equal(sendBlocker(state), null);
});

test("previously selected Ollama model removed and only one model left: that one is selected", () => {
  let state = onOllama(fresh(), ["a:1", "b:2"]);
  state = selectModel(state, "b:2");

  state = applyOllamaDiscovery(switchProvider(state, "ollama"), {
    ok: true,
    models: ["z:9"],
  });

  assert.equal(state.model, "z:9");
});

test("a discovery result arriving after switching away does not change the active selection", () => {
  let state = switchProvider(fresh(), "ollama");
  state = switchProvider(state, "openrouter");
  const before = state.model;

  state = applyOllamaDiscovery(state, { ok: true, models: ["late:1"] });

  assert.equal(state.provider, "openrouter");
  assert.equal(state.model, before);
  assert.equal(sendBlocker(state), null);
});

// ---------------------------------------------------------------- OpenRouter unchanged

test("OpenRouter defaults, dropdown and send behavior are unchanged", () => {
  const state = fresh();

  assert.equal(state.provider, "openrouter");
  assert.equal(state.model, "google/gemma-4-31b-it");
  assert.deepEqual(state.models.openrouter, BUILT_IN_MODELS.openrouter);
  assert.equal(sendBlocker(state), null);
});

test("OpenRouter: typed model IDs are added, selected, de-duplicated and removable", () => {
  let state = addModel(fresh(), "  vendor/custom-model:free  ");
  assert.equal(state.model, "vendor/custom-model:free");
  assert.deepEqual(state.models.openrouter, [
    ...BUILT_IN_MODELS.openrouter,
    "vendor/custom-model:free",
  ]);

  // Adding the same ID again does not duplicate it.
  state = addModel(state, "vendor/custom-model:free");
  assert.equal(state.models.openrouter.filter((m) => m === "vendor/custom-model:free").length, 1);

  // Blank input is ignored.
  assert.equal(addModel(state, "   "), state);

  // Built-in models cannot be removed; custom ones can.
  assert.equal(removeModel(state, BUILT_IN_MODELS.openrouter[0]), state);
  state = removeModel(state, "vendor/custom-model:free");
  assert.deepEqual(state.models.openrouter, BUILT_IN_MODELS.openrouter);
  assert.ok(BUILT_IN_MODELS.openrouter.includes(state.model) || state.model === "");
});

test("OpenRouter: cached models and the selected model persist across reloads", () => {
  let state = addModel(fresh(), "vendor/custom-model:free");
  state = selectModel(state, "deepseek/deepseek-v4-pro");
  const storage = { getItem: (key: string) => (key === STORAGE_KEY ? serializeState(state) : null) };

  const reloaded = loadPickerState(storage);

  assert.equal(reloaded.provider, "openrouter");
  assert.equal(reloaded.model, "deepseek/deepseek-v4-pro");
  assert.deepEqual(reloaded.models.openrouter, [
    ...BUILT_IN_MODELS.openrouter,
    "vendor/custom-model:free",
  ]);
});

test("OpenRouter: state saved by the previous picker version loads unchanged", () => {
  const legacy = JSON.stringify({
    provider: "openrouter",
    model: "vendor/old-custom",
    models: {
      openrouter: [...BUILT_IN_MODELS.openrouter, "vendor/old-custom"],
      ollama: ["stale-cached-ollama-model"],
      vllm: [],
      sglang: [],
    },
  });

  const state = parseStoredState(legacy);

  assert.equal(state.provider, "openrouter");
  assert.equal(state.model, "vendor/old-custom");
  assert.ok(state.models.openrouter.includes("vendor/old-custom"));
  // Ollama's list is discovered, never read from the cache.
  assert.deepEqual(state.models.ollama, []);
});

test("OpenRouter: switching to another provider still picks that provider's first cached model", () => {
  let state = addModel(fresh(), "x/y");
  state = switchProvider(state, "vllm");
  assert.equal(state.model, "");
  state = addModel(state, "local/model");
  state = switchProvider(state, "openrouter");
  assert.equal(state.model, BUILT_IN_MODELS.openrouter[0]);
  state = switchProvider(state, "vllm");
  assert.equal(state.model, "local/model");
  assert.equal(sendBlocker(state), null);
});

test("OpenRouter switch does not persist the discovered Ollama list", () => {
  const state = onOllama(fresh(), ["a:1", "b:2"]);
  const saved = JSON.parse(serializeState(state));

  assert.deepEqual(saved.models.ollama, []);
  assert.equal(saved.ollamaModel, "a:1");
  assert.equal("ollamaStatus" in saved, false);
});

test("stored Ollama provider reloads in a loading state and keeps its remembered model", () => {
  const state = onOllama(fresh(), ["a:1", "b:2"]);
  const picked = selectModel(state, "b:2");
  const reloaded = parseStoredState(serializeState(picked));

  assert.equal(reloaded.provider, "ollama");
  assert.equal(reloaded.model, "b:2");
  assert.deepEqual(reloaded.models.ollama, []);
  // Not sendable until discovery confirms the model still exists.
  assert.equal(sendBlocker(reloaded), OLLAMA_LOADING_MESSAGE);

  const confirmed = applyOllamaDiscovery(reloaded, { ok: true, models: ["a:1", "b:2"] });
  assert.equal(confirmed.model, "b:2");
  assert.equal(sendBlocker(confirmed), null);
});

test("corrupt or missing storage falls back to the OpenRouter defaults", () => {
  assert.equal(parseStoredState("{not json").provider, "openrouter");
  assert.equal(parseStoredState(null).model, "google/gemma-4-31b-it");
  assert.equal(
    loadPickerState({
      getItem: () => {
        throw new Error("storage blocked");
      },
    }).provider,
    "openrouter",
  );
});

// ---------------------------------------------------------------- Chat request bodies

interface CapturedRequest {
  url: string;
  body: Record<string, unknown>;
  headers: Record<string, string>;
}

async function loadClient() {
  const store = new Map<string, string>();
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  };
  return await import("../src/api/client.ts");
}

function stubFetch(responses: (url: string) => Response) {
  const calls: CapturedRequest[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({
      url,
      body: init?.body ? JSON.parse(String(init.body)) : {},
      headers: (init?.headers ?? {}) as Record<string, string>,
    });
    return responses(url);
  }) as typeof fetch;
  return { calls, restore: () => void (globalThis.fetch = original) };
}

test("chat request submits the selected Ollama provider and exact model ID", async () => {
  const client = await loadClient();
  const state = onOllama(fresh(), [HF_MODEL]);
  const net = stubFetch(
    () =>
      new Response(JSON.stringify({ session_id: "s", response: "ok", turn_id: "t" }), {
        status: 200,
      }),
  );

  try {
    await client.sendMessage("hello", "s1", undefined, state.provider, state.model);
  } finally {
    net.restore();
  }

  assert.equal(net.calls.length, 1);
  assert.equal(net.calls[0].body.provider_override, "ollama");
  assert.equal(net.calls[0].body.model_override, HF_MODEL);
});

test("streaming chat request submits the selected provider and exact model ID", async () => {
  const client = await loadClient();
  const state = onOllama(fresh(), ["llama3.2:latest", HF_MODEL]);
  const selected = selectModel(state, HF_MODEL);
  const net = stubFetch(
    () => new Response("data: [DONE]\n\n", { status: 200, headers: { "content-type": "text/event-stream" } }),
  );

  try {
    await client.streamChat("s1", "hello", {}, undefined, undefined, selected.provider, selected.model);
  } finally {
    net.restore();
  }

  assert.equal(net.calls[0].body.provider_override, "ollama");
  assert.equal(net.calls[0].body.model_override, HF_MODEL);
});

test("chat requests follow the picker when switching Ollama <-> OpenRouter", async () => {
  const client = await loadClient();
  const net = stubFetch(
    () =>
      new Response(JSON.stringify({ session_id: "s", response: "ok", turn_id: "t" }), {
        status: 200,
      }),
  );

  try {
    let state = onOllama(fresh(), [HF_MODEL]);
    await client.sendMessage("one", "s1", undefined, state.provider, state.model);

    state = switchProvider(state, "openrouter");
    state = selectModel(state, "deepseek/deepseek-v4-pro");
    await client.sendMessage("two", "s1", undefined, state.provider, state.model);

    state = applyOllamaDiscovery(switchProvider(state, "ollama"), { ok: true, models: [HF_MODEL] });
    await client.sendMessage("three", "s1", undefined, state.provider, state.model);
  } finally {
    net.restore();
  }

  assert.deepEqual(
    net.calls.map((call) => [call.body.provider_override, call.body.model_override]),
    [
      ["ollama", HF_MODEL],
      ["openrouter", "deepseek/deepseek-v4-pro"],
      ["ollama", HF_MODEL],
    ],
  );
});

test("fetchOllamaModels calls the chatbot backend (not Ollama) and returns IDs unchanged", async () => {
  const client = await loadClient();
  client.setAccessToken("tok");
  const net = stubFetch(
    () => new Response(JSON.stringify({ provider: "ollama", models: [HF_MODEL, "llama3.2:latest"] }), { status: 200 }),
  );

  let models: string[];
  try {
    models = await client.fetchOllamaModels();
  } finally {
    net.restore();
  }

  assert.deepEqual(models, [HF_MODEL, "llama3.2:latest"]);
  assert.equal(net.calls[0].url, "/api/models/ollama");
  assert.equal(net.calls[0].headers["Authorization"], "Bearer tok");
  assert.doesNotMatch(net.calls[0].url, /11434/);
});

test("fetchOllamaModels surfaces the backend's connection error", async () => {
  const client = await loadClient();
  const net = stubFetch(
    () => new Response(JSON.stringify({ detail: UNREACHABLE }), { status: 502 }),
  );

  let caught: unknown;
  try {
    await client.fetchOllamaModels();
  } catch (error) {
    caught = error;
  } finally {
    net.restore();
  }

  assert.equal(ollamaConnectionMessage(caught), UNREACHABLE);
});

// ---------------------------------------------------------------- Avatar (spoken) turns

test("avatar: the popup reads the picker's persisted selection (OpenRouter default)", () => {
  assert.deepEqual(routingFromStorage(null), {
    provider_override: "openrouter",
    model_override: "google/gemma-4-31b-it",
    thinking: false,
    blocked: null,
  });
});

test("avatar: an Ollama selection reaches the popup unchanged via storage", () => {
  const state = selectModel(onOllama(fresh(), ["a:1", HF_MODEL]), HF_MODEL);

  assert.deepEqual(routingFromStorage(serializeState(state)), {
    provider_override: "ollama",
    model_override: HF_MODEL,
    thinking: false,
    blocked: null,
  });
});

test("avatar: switching back to OpenRouter is reflected, never stuck on Ollama", () => {
  let state = selectModel(onOllama(fresh(), [HF_MODEL]), HF_MODEL);
  state = switchProvider(state, "openrouter");
  state = selectModel(state, "deepseek/deepseek-v4-pro");

  const routing = routingFromStorage(serializeState(state));

  assert.equal(routing.provider_override, "openrouter");
  assert.equal(routing.model_override, "deepseek/deepseek-v4-pro");
});

test("avatar: Ollama with no model selected is reported as blocked, not defaulted", () => {
  const state = { ...switchProvider(fresh(), "ollama"), model: "", ollamaModel: "" };

  const routing = routingSelection(state);

  assert.equal(routing.provider_override, "ollama");
  assert.equal(routing.model_override, null);
  assert.equal(routing.blocked, "Select an Ollama model");
});

test("avatar: the handshake carries the picker selection", () => {
  const routing = routingFromStorage(
    serializeState(selectModel(onOllama(fresh(), [HF_MODEL]), HF_MODEL)),
  );

  const auth = buildAuthMessage(
    { token: "tok", sessionId: "s1", companionId: "c1", roomId: null, tabId: "tab" },
    routing,
  );

  assert.equal(auth.type, "auth");
  assert.equal(auth.session_id, "s1");
  assert.equal(auth.provider_override, "ollama");
  assert.equal(auth.model_override, HF_MODEL);
  assert.equal(auth.blocked, null);
});

test("avatar: a handshake without a routing source stays as before", () => {
  const auth = buildAuthMessage(
    { token: "tok", sessionId: "s1", companionId: "c1", roomId: null, tabId: "tab" },
    null,
  );

  assert.equal("provider_override" in auth, false);
  assert.equal("model_override" in auth, false);
});

test("avatar: live picker changes become routing messages with exact model IDs", () => {
  const message = buildRoutingMessage(
    routingFromStorage(serializeState(selectModel(onOllama(fresh(), [HF_MODEL]), HF_MODEL))),
  );

  assert.deepEqual(message, {
    type: "routing",
    provider_override: "ollama",
    model_override: HF_MODEL,
    thinking: false,
    blocked: null,
  });
});

test("thinking persists across refresh, model and provider switches and popup reconnect", () => {
  assert.equal(fresh().thinking, false);
  let state = { ...onOllama(fresh(), ["a:1", HF_MODEL]), thinking: true };
  state = selectModel(state, HF_MODEL);
  state = switchProvider(switchProvider(state, "openrouter"), "ollama");
  const restored = parseStoredState(serializeState(state));
  assert.equal(restored.thinking, true);
  const routing = routingFromStorage(serializeState(restored));
  assert.equal(buildRoutingMessage(routing).thinking, true);
  assert.equal(buildAuthMessage({ token: null, sessionId: "s", companionId: "c", roomId: null, tabId: "t" }, routing).thinking, true);
});
