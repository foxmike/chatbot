// Pure state logic for the provider-aware model picker.
//
// OpenRouter (and vllm/sglang) keep the cached, user-extendable model list.
// Ollama models are discovered from the backend each time Ollama is selected,
// never typed in and never cached as a list. Nothing here ever substitutes one
// provider or model for another.

export const STORAGE_KEY = "chatbot_model_picker";
export const PROVIDERS = ["openrouter", "ollama", "vllm", "sglang"] as const;

export type Provider = (typeof PROVIDERS)[number];
export type ModelLists = Record<Provider, string[]>;

export const BUILT_IN_MODELS: ModelLists = {
  openrouter: [
    "google/gemma-4-31b-it",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3-235b-a22b",
  ],
  ollama: [],
  vllm: [],
  sglang: [],
};

export type OllamaStatus =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "empty" }
  | { kind: "error"; message: string };

export interface PickerState {
  provider: Provider;
  thinking: boolean;
  model: string;
  models: ModelLists;
  // The Ollama model the user last chose; survives switching to other providers.
  ollamaModel: string;
  // Runtime only; never persisted.
  ollamaStatus: OllamaStatus;
}

export type OllamaDiscovery =
  | { ok: true; models: string[] }
  | { ok: false; message: string };

export const OLLAMA_LOADING_MESSAGE = "Loading Ollama models…";
export const OLLAMA_EMPTY_MESSAGE = "No Ollama models available";

export function isProvider(value: unknown): value is Provider {
  return typeof value === "string" && PROVIDERS.includes(value as Provider);
}

function emptyLists(): ModelLists {
  return { openrouter: [], ollama: [], vllm: [], sglang: [] };
}

function freshState(): PickerState {
  return {
    provider: "openrouter",
    thinking: false,
    model: BUILT_IN_MODELS.openrouter[0],
    models: {
      openrouter: [...BUILT_IN_MODELS.openrouter],
      ollama: [],
      vllm: [],
      sglang: [],
    },
    ollamaModel: "",
    ollamaStatus: { kind: "idle" },
  };
}

function initialOllamaStatus(provider: Provider): OllamaStatus {
  return provider === "ollama" ? { kind: "loading" } : { kind: "idle" };
}

/** Rebuild picker state from the persisted JSON (or defaults when absent/corrupt). */
export function parseStoredState(raw: string | null): PickerState {
  const fallback = freshState();
  if (!raw) return fallback;

  try {
    const parsed = JSON.parse(raw) as Partial<PickerState>;
    const models = PROVIDERS.reduce<ModelLists>((lists, provider) => {
      const storedModels = Array.isArray(parsed.models?.[provider])
        ? parsed.models[provider].filter(
            (model): model is string => typeof model === "string" && model.length > 0,
          )
        : [];
      lists[provider] = Array.from(
        new Set([...BUILT_IN_MODELS[provider], ...storedModels]),
      );
      return lists;
    }, emptyLists());
    // Ollama models come from discovery, never from the cache.
    models.ollama = [];

    const provider = isProvider(parsed.provider) ? parsed.provider : fallback.provider;
    const storedModel = typeof parsed.model === "string" ? parsed.model : "";
    const ollamaModel =
      typeof parsed.ollamaModel === "string"
        ? parsed.ollamaModel
        : parsed.provider === "ollama"
          ? storedModel
          : "";
    const model =
      provider === "ollama"
        ? ollamaModel
        : models[provider].includes(storedModel)
          ? storedModel
          : (models[provider][0] ?? "");

    return {
      provider,
      thinking: parsed.thinking === true,
      model,
      models,
      ollamaModel,
      ollamaStatus: initialOllamaStatus(provider),
    };
  } catch {
    return fallback;
  }
}

export function loadPickerState(storage: Pick<Storage, "getItem"> | null): PickerState {
  try {
    return parseStoredState(storage ? storage.getItem(STORAGE_KEY) : null);
  } catch {
    return freshState();
  }
}

/** What gets persisted: everything except runtime status and the Ollama list. */
export function serializeState(state: PickerState): string {
  return JSON.stringify({
    provider: state.provider,
    thinking: state.thinking,
    model: state.model,
    models: { ...state.models, ollama: [] },
    ollamaModel: state.ollamaModel,
  });
}

export function switchProvider(state: PickerState, provider: Provider): PickerState {
  if (provider === "ollama") {
    // The list is re-discovered on every selection; until it arrives nothing is
    // selectable (and sending is blocked) so a stale list can never be used.
    return {
      ...state,
      provider,
      model: state.ollamaModel,
      models: { ...state.models, ollama: [] },
      ollamaStatus: { kind: "loading" },
    };
  }
  return {
    ...state,
    provider,
    model: state.models[provider][0] ?? "",
    ollamaStatus: { kind: "idle" },
  };
}

export function selectModel(state: PickerState, model: string): PickerState {
  return {
    ...state,
    model,
    ollamaModel: state.provider === "ollama" ? model : state.ollamaModel,
  };
}

/** Add a user-typed model ID. Not available for Ollama (its list is discovered). */
export function addModel(state: PickerState, rawModel: string): PickerState {
  const model = rawModel.trim();
  if (!model || state.provider === "ollama") return state;

  return {
    ...state,
    model,
    models: {
      ...state.models,
      [state.provider]: state.models[state.provider].includes(model)
        ? state.models[state.provider]
        : [...state.models[state.provider], model],
    },
  };
}

export function removeModel(state: PickerState, model: string): PickerState {
  if (state.provider === "ollama") return state;
  if (BUILT_IN_MODELS[state.provider].includes(model)) return state;

  const currentModels = state.models[state.provider];
  const removedIndex = currentModels.indexOf(model);
  const models = currentModels.filter((entry) => entry !== model);
  return {
    ...state,
    model:
      state.model === model
        ? (models[removedIndex] ?? models[removedIndex - 1] ?? "")
        : state.model,
    models: { ...state.models, [state.provider]: models },
  };
}

/**
 * Apply the result of an Ollama model discovery.
 *
 * Model IDs are used exactly as Ollama returned them. The remembered model is
 * kept when it is still installed; otherwise the first available model is
 * selected (which is also how a lone model is auto-selected).
 */
export function applyOllamaDiscovery(
  state: PickerState,
  discovery: OllamaDiscovery,
): PickerState {
  const onOllama = state.provider === "ollama";

  if (!discovery.ok) {
    return {
      ...state,
      model: onOllama ? "" : state.model,
      models: { ...state.models, ollama: [] },
      ollamaStatus: { kind: "error", message: discovery.message },
    };
  }

  const ids = Array.from(new Set(discovery.models.filter((id) => id.length > 0)));
  if (ids.length === 0) {
    return {
      ...state,
      model: onOllama ? "" : state.model,
      models: { ...state.models, ollama: [] },
      ollamaStatus: { kind: "empty" },
    };
  }

  const ollamaModel = ids.includes(state.ollamaModel) ? state.ollamaModel : ids[0];
  return {
    ...state,
    model: onOllama ? ollamaModel : state.model,
    models: { ...state.models, ollama: ids },
    ollamaModel,
    ollamaStatus: { kind: "ready" },
  };
}

/** A user-facing reason sending must be blocked, or null when sending is allowed. */
export function sendBlocker(state: PickerState): string | null {
  if (state.provider !== "ollama") return null;

  switch (state.ollamaStatus.kind) {
    case "loading":
    case "idle":
      return OLLAMA_LOADING_MESSAGE;
    case "empty":
      return OLLAMA_EMPTY_MESSAGE;
    case "error":
      return state.ollamaStatus.message;
    case "ready":
      return state.model && state.models.ollama.includes(state.model)
        ? null
        : "Select an Ollama model";
  }
}

export function ollamaConnectionMessage(error: unknown): string {
  let detail = "";
  if (error instanceof Error) {
    detail = error.message;
  } else if (typeof error === "string") {
    detail = error;
  } else if (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { detail?: unknown }).detail === "string"
  ) {
    // The API client throws { detail, status_code } objects.
    detail = (error as { detail: string }).detail;
  }

  if (detail && /ollama/i.test(detail)) return detail;
  return `Cannot connect to Ollama${detail ? `: ${detail}` : ""}`;
}

/**
 * The picker's selection in the form the avatar socket (and chat requests) carry
 * it. Model IDs are passed through unchanged; nothing here picks a substitute.
 */
export interface RoutingSelection {
  provider_override: string | null;
  model_override: string | null;
  thinking: boolean;
  /** A user-facing reason this selection cannot be used, or null. */
  blocked: string | null;
}

export const OLLAMA_NO_MODEL_MESSAGE = "Select an Ollama model";

export function routingSelection(state: PickerState): RoutingSelection {
  return {
    provider_override: state.provider,
    model_override: state.model || null,
    thinking: state.thinking,
    blocked: state.provider === "ollama" && !state.model ? OLLAMA_NO_MODEL_MESSAGE : null,
  };
}

/** Selection from the persisted picker state, for windows that don't host the picker. */
export function routingFromStorage(raw: string | null): RoutingSelection {
  return routingSelection(parseStoredState(raw));
}
