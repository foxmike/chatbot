import { useEffect, useState } from "react";

const STORAGE_KEY = "chatbot_model_picker";
const PROVIDERS = ["openrouter", "ollama", "vllm", "sglang"] as const;

type Provider = (typeof PROVIDERS)[number];
type ModelLists = Record<Provider, string[]>;

const BUILT_IN_MODELS: ModelLists = {
  openrouter: [
    "google/gemma-4-31b-it",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3-235b-a22b",
  ],
  ollama: [],
  vllm: [],
  sglang: [],
};

interface PickerState {
  provider: Provider;
  model: string;
  models: ModelLists;
}

export interface ModelPickerProps {
  onChange: (provider: string, model: string) => void;
}

function isProvider(value: unknown): value is Provider {
  return typeof value === "string" && PROVIDERS.includes(value as Provider);
}

function initialState(): PickerState {
  const fallback: PickerState = {
    provider: "openrouter",
    model: BUILT_IN_MODELS.openrouter[0],
    models: {
      openrouter: [...BUILT_IN_MODELS.openrouter],
      ollama: [],
      vllm: [],
      sglang: [],
    },
  };

  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return fallback;

    const parsed = JSON.parse(stored) as Partial<PickerState>;
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
    }, { openrouter: [], ollama: [], vllm: [], sglang: [] });
    const provider = isProvider(parsed.provider) ? parsed.provider : fallback.provider;
    const storedModel = typeof parsed.model === "string" ? parsed.model : "";
    const model = models[provider].includes(storedModel)
      ? storedModel
      : (models[provider][0] ?? "");

    return { provider, model, models };
  } catch {
    return fallback;
  }
}

export function ModelPicker({ onChange }: ModelPickerProps) {
  const [picker, setPicker] = useState<PickerState>(initialState);
  const [modelInput, setModelInput] = useState("");

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(picker));
    onChange(picker.provider, picker.model);
  }, [onChange, picker]);

  const addModel = () => {
    const model = modelInput.trim();
    if (!model) return;

    setPicker((current) => ({
      ...current,
      model,
      models: {
        ...current.models,
        [current.provider]: current.models[current.provider].includes(model)
          ? current.models[current.provider]
          : [...current.models[current.provider], model],
      },
    }));
    setModelInput("");
  };

  const removeModel = (model: string) => {
    if (BUILT_IN_MODELS[picker.provider].includes(model)) return;

    setPicker((current) => {
      const currentModels = current.models[current.provider];
      const removedIndex = currentModels.indexOf(model);
      const models = currentModels.filter((entry) => entry !== model);
      return {
        ...current,
        model:
          current.model === model
            ? (models[removedIndex] ?? models[removedIndex - 1] ?? "")
            : current.model,
        models: { ...current.models, [current.provider]: models },
      };
    });
  };

  const selectedModelIsCustom =
    picker.model.length > 0 &&
    !BUILT_IN_MODELS[picker.provider].includes(picker.model);

  return (
    <div className="mb-2 flex min-w-0 items-center gap-2 overflow-x-auto text-xs text-gray-300">
      <select
        aria-label="LLM provider"
        value={picker.provider}
        className="h-8 shrink-0 rounded-md border border-gray-600 bg-gray-900 px-2 text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        onChange={(event) => {
          const provider = event.target.value as Provider;
          setPicker((current) => ({
            ...current,
            provider,
            model: current.models[provider][0] ?? "",
          }));
        }}
      >
        {PROVIDERS.map((provider) => (
          <option key={provider} value={provider}>
            {provider}
          </option>
        ))}
      </select>
      <select
        aria-label="LLM model"
        value={picker.model}
        className="h-8 min-w-36 max-w-64 shrink rounded-md border border-gray-600 bg-gray-900 px-2 text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        onChange={(event) => {
          const model = event.target.value;
          setPicker((current) => ({ ...current, model }));
        }}
      >
        {picker.models[picker.provider].length === 0 ? (
          <option value="">Type to add…</option>
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
    </div>
  );
}

export default ModelPicker;
