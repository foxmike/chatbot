import { useEffect, useState } from "react";
import { accountLocalStorage, fetchOllamaModels } from "../api/client";
import {
  BUILT_IN_MODELS,
  PROVIDERS,
  STORAGE_KEY,
  addModel as addModelToState,
  applyOllamaDiscovery,
  loadPickerState,
  ollamaConnectionMessage,
  removeModel as removeModelFromState,
  selectModel,
  sendBlocker,
  serializeState,
  switchProvider,
  type PickerState,
  type Provider,
} from "../utils/modelPicker";

export interface ModelPickerProps {
  /**
   * Reports the selection. `sendBlocker` is a user-facing reason sending must
   * be prevented (e.g. Ollama unreachable), or null when sending is allowed.
   */
  onChange: (provider: string, model: string, sendBlocker: string | null, thinking: boolean) => void;
}

function readStorage(): PickerState {
  try {
    return loadPickerState(accountLocalStorage());
  } catch {
    return loadPickerState(null);
  }
}

export function ModelPicker({ onChange }: ModelPickerProps) {
  const [picker, setPicker] = useState<PickerState>(readStorage);
  const [modelInput, setModelInput] = useState("");
  const isOllama = picker.provider === "ollama";
  const blocker = sendBlocker(picker);

  useEffect(() => {
    try {
      accountLocalStorage().setItem(STORAGE_KEY, serializeState(picker));
    } catch {
      // Persistence is best-effort.
    }
  }, [picker]);

  useEffect(() => {
    onChange(picker.provider, picker.model, blocker, picker.thinking);
  }, [onChange, picker.provider, picker.model, blocker, picker.thinking]);

  // Re-discover Ollama's models every time Ollama is selected (and on load when
  // it is the stored provider). Never falls back to another provider.
  useEffect(() => {
    if (!isOllama) return;

    const controller = new AbortController();
    fetchOllamaModels(controller.signal)
      .then((models) => {
        if (controller.signal.aborted) return;
        setPicker((current) => applyOllamaDiscovery(current, { ok: true, models }));
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setPicker((current) =>
          applyOllamaDiscovery(current, {
            ok: false,
            message: ollamaConnectionMessage(error),
          }),
        );
      });

    return () => controller.abort();
  }, [isOllama]);

  const addModel = () => {
    setPicker((current) => addModelToState(current, modelInput));
    setModelInput("");
  };

  const removeModel = (model: string) => {
    setPicker((current) => removeModelFromState(current, model));
  };

  const selectedModelIsCustom =
    !isOllama &&
    picker.model.length > 0 &&
    !BUILT_IN_MODELS[picker.provider].includes(picker.model);

  const ollamaStatusKind = picker.ollamaStatus.kind;
  const emptyOptionLabel = isOllama
    ? ollamaStatusKind === "loading" || ollamaStatusKind === "idle"
      ? "Loading Ollama models…"
      : ollamaStatusKind === "error"
        ? "Ollama unavailable"
        : "No Ollama models available"
    : "Type to add…";

  return (
    <div className="mb-2 min-w-0 text-xs text-gray-300">
      <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
        <select
          aria-label="LLM provider"
          value={picker.provider}
          className="h-8 shrink-0 rounded-md border border-gray-600 bg-gray-900 px-2 text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          onChange={(event) => {
            const provider = event.target.value as Provider;
            setPicker((current) => switchProvider(current, provider));
          }}
        >
          {PROVIDERS.map((provider) => (
            <option key={provider} value={provider}>
              {provider}
            </option>
          ))}
        </select>
        <label className="flex shrink-0 items-center gap-1">
          Thinking:
          <select aria-label="Thinking" value={picker.thinking ? "on" : "off"}
            className="h-8 rounded-md border border-gray-600 bg-gray-900 px-2 text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            onChange={(event) => setPicker((current) => ({ ...current, thinking: event.target.value === "on" }))}>
            <option value="off">Off</option><option value="on">On</option>
          </select>
        </label>
        <select
          aria-label="LLM model"
          value={picker.model}
          className="h-8 min-w-36 max-w-64 shrink rounded-md border border-gray-600 bg-gray-900 px-2 text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          onChange={(event) => {
            const model = event.target.value;
            setPicker((current) => selectModel(current, model));
          }}
        >
          {picker.models[picker.provider].length === 0 ? (
            <option value="">{emptyOptionLabel}</option>
          ) : null}
          {picker.models[picker.provider].map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </select>
        {selectedModelIsCustom ? (
          <button
            type="button"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-gray-600 bg-gray-900 text-gray-500 hover:border-indigo-500 hover:text-indigo-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            aria-label={`Remove ${picker.model}`}
            title={`Remove ${picker.model}`}
            onClick={() => removeModel(picker.model)}
          >
            ×
          </button>
        ) : null}
        {isOllama ? null : (
          <input
            aria-label="Add model"
            value={modelInput}
            placeholder="Add model…"
            className="h-8 w-32 shrink-0 rounded-md border border-gray-600 bg-gray-900 px-2 text-gray-100 placeholder:text-gray-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            onChange={(event) => setModelInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addModel();
              }
            }}
          />
        )}
      </div>
      {!isOllama ? <p className="mt-1 text-gray-400">Thinking control is only available for Ollama; other providers use their defaults.</p> : null}
      {isOllama ? <p className="mt-1 text-gray-400">Thinking support varies by Ollama model and server version.</p> : null}
      {isOllama && blocker && ollamaStatusKind !== "loading" ? (
        <p
          role="alert"
          className="mt-1 text-red-400"
        >
          {blocker}
        </p>
      ) : null}
    </div>
  );
}

export default ModelPicker;
