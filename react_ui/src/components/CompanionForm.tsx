import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  createCompanion,
  deleteVisualImage,
  generateCompanionField,
  generateVisualImage,
  listCompanionImages,
  updateCompanion,
  updateVisualImageDescription,
  uploadVisualImage,
} from "../api/client";
import type {
  Companion,
  CompanionCreate,
  GeneratedCompanionImage,
  GeneratedCompanionPersonality,
  GenerateCompanionFieldName,
  GenerateVisualImageResponse,
} from "../api/client";
import type { ApiError } from "../types";
import { avatarColorForKey, getInitial } from "../utils/avatar";
import { listVoices } from "../api/voices";
import type { Voice } from "../api/voices";
import { companionVoiceOptions, voiceIdForValue } from "../utils/voices";

const PERSONALITY_DIMS = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
  "curiosity",
  "creativity",
] as const;

const HUMOR_STYLES = ["dry", "witty", "silly", "dark", "none"] as const;
const COMM_STYLES = [
  "verbose",
  "concise",
  "storyteller",
  "question_driven",
  "aphoristic",
] as const;
const FINGERPRINT_FIELDS = [
  {
    key: "punctuation_habits",
    label: "Punctuation habits",
    placeholder: '{"ellipsis_usage":"frequent","capitalization":"lowercase"}',
  },
  {
    key: "sentence_length_profile",
    label: "Sentence length profile",
    placeholder: '{"preference":"short","max_words":12}',
  },
  {
    key: "emoji_style",
    label: "Emoji style",
    placeholder: '{"frequency":"rare","placement":"end"}',
  },
  {
    key: "slang_profile",
    label: "Slang profile",
    placeholder: '{"frequency":"occasional","type":"gen_z"}',
  },
] as const;

const DEFAULT_SLIDERS: Record<string, number> = {
  openness: 0.7,
  conscientiousness: 0.6,
  extraversion: 0.5,
  agreeableness: 0.8,
  neuroticism: 0.3,
  curiosity: 0.7,
  creativity: 0.6,
};

const INPUT =
  "rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60";
const LABEL = "flex flex-col gap-2 text-sm font-medium text-gray-200";
const AUTO_LINK =
  "ml-2 align-baseline text-xs font-medium text-cyan-400 transition hover:text-cyan-300 hover:underline focus:outline-none focus:ring-2 focus:ring-cyan-500/40";
const COMPANION_AUTO_PROVIDER_KEY = "companionAutoProvider";
const COMPANION_AUTO_MODEL_KEY = "companionAutoModel";
const AUTO_PROVIDER_OPTIONS = ["openrouter", "ollama", "vllm", "sglang"] as const;
const SINGLE_MODEL_AUTO_PROVIDERS = new Set(["ollama", "vllm", "sglang"]);
const AUTO_MODEL_OPTIONS = [
  "google/gemma-4-31b-it",
  "openrouter/deepseek/deepseek-v4-pro",
  "openrouter/deepseek/deepseek-v3.2",
  "openrouter/qwen/qwen3-coder-next",
] as const;

interface CompanionFormData {
  name: string;
  gender: "male" | "female";
  persona: string;
  backstory: string;
  personality_sliders: Record<string, number>;
  humor_style: string;
  communication_style: string;
  punctuation_habits: string;
  sentence_length_profile: string;
  emoji_style: string;
  vocabulary_density: number | null;
  slang_profile: string;
  adaptive_verbosity: number | null;
  favorite_topics: string[];
  avatar_url: string | null;
  avatar_header: string;
  voice_id: string;          // "" = the built-in default voice
}

interface CompanionFormProps {
  mode: "create" | "edit";
  companion?: Companion;
  showCancel?: boolean;
  onCancel?: () => void;
  onSave?: (companion: Companion) => void;
}

function emptyForm(): CompanionFormData {
  return {
    name: "",
    gender: "female",
    persona: "",
    backstory: "",
    personality_sliders: { ...DEFAULT_SLIDERS },
    humor_style: "witty",
    communication_style: "concise",
    punctuation_habits: "",
    sentence_length_profile: "",
    emoji_style: "",
    vocabulary_density: null,
    slang_profile: "",
    adaptive_verbosity: null,
    favorite_topics: [],
    avatar_url: null,
    avatar_header: "",
    voice_id: "",
  };
}

function jsonObjectToFormValue(value: Record<string, unknown> | null | undefined): string {
  return value ? JSON.stringify(value, null, 2) : "";
}

function companionToForm(companion: Companion | undefined): CompanionFormData {
  if (!companion) return emptyForm();
  return {
    name: companion.name,
    gender: companion.gender ?? "female",
    persona: companion.persona ?? "",
    backstory: companion.backstory ?? "",
    personality_sliders: companion.personality_sliders
      ? { ...DEFAULT_SLIDERS, ...companion.personality_sliders }
      : { ...DEFAULT_SLIDERS },
    humor_style: companion.humor_style ?? "witty",
    communication_style: companion.communication_style ?? "concise",
    punctuation_habits: jsonObjectToFormValue(companion.punctuation_habits),
    sentence_length_profile: jsonObjectToFormValue(companion.sentence_length_profile),
    emoji_style: jsonObjectToFormValue(companion.emoji_style),
    vocabulary_density: companion.vocabulary_density ?? null,
    slang_profile: jsonObjectToFormValue(companion.slang_profile),
    adaptive_verbosity: companion.adaptive_verbosity ?? null,
    favorite_topics: companion.favorite_topics ?? [],
    avatar_url: companion.avatar_url ?? null,
    avatar_header: companion.avatar_header ?? "",
    voice_id: companion.voice_id ?? "",
  };
}

function getErrorDetail(error: unknown): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "detail" in error &&
    typeof (error as ApiError).detail === "string"
  ) {
    return (error as ApiError).detail;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

function fmtLabel(value: string) {
  return value.replace(/_/g, " ");
}

function parseJsonObjectField(label: string, value: string): Record<string, unknown> | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("not an object");
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new Error(`${label} must be a valid JSON object.`);
  }
}

function generatedImageUrl(image: GenerateVisualImageResponse): string {
  return image.image_url ?? `data:${image.mime_type};base64,${image.image_base64}`;
}

function generatedImageToGalleryItem(
  image: GenerateVisualImageResponse,
  imageUrl: string,
): GeneratedCompanionImage {
  return {
    image_id: image.image_id ?? `generated-${Date.now()}`,
    image_url: imageUrl,
    mime_type: image.mime_type,
    prompt_used: image.prompt_used,
    model_used: image.model_used,
    width: image.width,
    height: image.height,
    created_at: new Date().toISOString(),
  };
}

function imageDescription(image: GeneratedCompanionImage): string {
  return image.prompt_used ?? "";
}

function truncateText(value: string, maxLength = 72): string {
  const trimmed = value.trim();
  if (trimmed.length <= maxLength) return trimmed;
  return `${trimmed.slice(0, maxLength - 3).trimEnd()}...`;
}

function companionAutoRouting(provider: string, model: string) {
  const providerOverride = provider.trim();
  const modelDisabled = SINGLE_MODEL_AUTO_PROVIDERS.has(providerOverride.toLowerCase());
  return {
    ...(!modelDisabled && model.trim() ? { model_override: model.trim() } : {}),
    ...(providerOverride ? { provider_override: providerOverride } : {}),
  };
}

function normalizeGeneratedPersonality(content: unknown): GeneratedCompanionPersonality {
  const data =
    typeof content === "object" && content !== null
      ? (content as Record<string, unknown>)
      : {};
  const rawSliders =
    typeof data.sliders === "object" && data.sliders !== null
      ? (data.sliders as Record<string, unknown>)
      : typeof data.personality_sliders === "object" && data.personality_sliders !== null
        ? (data.personality_sliders as Record<string, unknown>)
        : {};
  const sliders = PERSONALITY_DIMS.reduce<Record<string, number>>((next, dim) => {
    const raw = rawSliders[dim];
    const value = typeof raw === "number" ? raw : Number(raw);
    next[dim] = Number.isFinite(value)
      ? Math.min(1, Math.max(0, value))
      : DEFAULT_SLIDERS[dim];
    return next;
  }, {});
  const humorStyle =
    typeof data.humor_style === "string" &&
    HUMOR_STYLES.includes(data.humor_style as (typeof HUMOR_STYLES)[number])
      ? data.humor_style
      : "witty";
  const communicationStyle =
    typeof data.communication_style === "string" &&
    COMM_STYLES.includes(data.communication_style as (typeof COMM_STYLES)[number])
      ? data.communication_style
      : "concise";

  return {
    humor_style: humorStyle,
    communication_style: communicationStyle,
    sliders,
  };
}

function firstSentences(value: string, maxSentences = 2): string {
  const trimmed = value.trim().replace(/\s+/g, " ");
  if (!trimmed) return "";
  const matches = trimmed.match(/[^.!?]+[.!?]+|[^.!?]+$/g);
  return (matches ?? [trimmed]).slice(0, maxSentences).join(" ").trim();
}

function personalityHighlights(sliders: Record<string, number>): string {
  const parts: string[] = [];
  const extraversion = sliders.extraversion ?? DEFAULT_SLIDERS.extraversion;
  const creativity = sliders.creativity ?? DEFAULT_SLIDERS.creativity;
  const curiosity = sliders.curiosity ?? DEFAULT_SLIDERS.curiosity;
  const openness = sliders.openness ?? DEFAULT_SLIDERS.openness;
  const agreeableness = sliders.agreeableness ?? DEFAULT_SLIDERS.agreeableness;
  const conscientiousness =
    sliders.conscientiousness ?? DEFAULT_SLIDERS.conscientiousness;
  const neuroticism = sliders.neuroticism ?? DEFAULT_SLIDERS.neuroticism;

  if (creativity >= 0.75) parts.push("highly creative");
  if (curiosity >= 0.75) parts.push("very curious");
  if (openness >= 0.75) parts.push("open-minded");
  if (extraversion >= 0.7) parts.push("outgoing");
  if (extraversion <= 0.35) parts.push("introverted");
  if (agreeableness >= 0.75) parts.push("warm");
  if (conscientiousness >= 0.75) parts.push("composed");
  if (neuroticism >= 0.7) parts.push("emotionally intense");
  if (neuroticism <= 0.3) parts.push("calm");

  return parts.length > 0 ? parts.join(", ") : "balanced and expressive";
}

function FieldTitle({
  children,
  field,
  loadingField,
  disabled,
  onAuto,
}: {
  children: string;
  field: GenerateCompanionFieldName;
  loadingField: GenerateCompanionFieldName | null;
  disabled: boolean;
  onAuto: (field: GenerateCompanionFieldName) => void;
}) {
  const isLoading = loadingField === field;
  return (
    <span>
      {children}
      <a
        href="#"
        aria-disabled={disabled || isLoading}
        className={[
          AUTO_LINK,
          disabled || isLoading ? "pointer-events-none opacity-60" : "",
        ].join(" ")}
        onClick={(event) => {
          event.preventDefault();
          if (!disabled && !isLoading) onAuto(field);
        }}
      >
        {isLoading ? "..." : "auto"}
      </a>
    </span>
  );
}

export function CompanionForm({
  mode,
  companion,
  showCancel = false,
  onCancel,
  onSave,
}: CompanionFormProps) {
  const companionId = companion?.companion_id;
  const [form, setForm] = useState<CompanionFormData>(() => companionToForm(companion));
  const [voices, setVoices] = useState<Voice[]>([]);

  useEffect(() => {
    listVoices()
      .then((library) => setVoices(library.voices))
      .catch(() => setVoices([]));
  }, []);
  const [topicInput, setTopicInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [generatingGalleryImage, setGeneratingGalleryImage] = useState(false);
  const [savingImageDescription, setSavingImageDescription] = useState(false);
  const [galleryLoading, setGalleryLoading] = useState(mode === "edit");
  const [galleryPrompt, setGalleryPrompt] = useState(companion?.description ?? "");
  const [galleryImages, setGalleryImages] = useState<GeneratedCompanionImage[]>([]);
  const [activeGalleryImageId, setActiveGalleryImageId] = useState<string | null>(null);
  const [autoLoadingField, setAutoLoadingField] =
    useState<GenerateCompanionFieldName | null>(null);
  const [autoProvider, setAutoProvider] = useState(() =>
    localStorage.getItem(COMPANION_AUTO_PROVIDER_KEY) ?? "",
  );
  const [autoModel, setAutoModel] = useState(() =>
    localStorage.getItem(COMPANION_AUTO_MODEL_KEY) ?? "",
  );
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const clearSavedRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isAutoModelDisabled = SINGLE_MODEL_AUTO_PROVIDERS.has(
    autoProvider.trim().toLowerCase(),
  );
  const avatarImagePrompt = () =>
    [form.avatar_header.trim(), galleryPrompt.trim()].filter(Boolean).join("\n");
  const galleryFooterText = (image: GeneratedCompanionImage) =>
    form.avatar_header.trim() ||
    truncateText(imageDescription(image)) ||
    "No description";

  useEffect(() => {
    setError(null);
    setSaved(false);
    setTopicInput("");
    setGalleryPrompt(companion?.description ?? "");
    setActiveGalleryImageId(null);
    setForm(companionToForm(companion));
  }, [companion]);

  useEffect(() => {
    if (autoProvider.trim()) {
      localStorage.setItem(COMPANION_AUTO_PROVIDER_KEY, autoProvider);
    } else {
      localStorage.removeItem(COMPANION_AUTO_PROVIDER_KEY);
    }
  }, [autoProvider]);

  useEffect(() => {
    if (autoModel.trim()) {
      localStorage.setItem(COMPANION_AUTO_MODEL_KEY, autoModel);
    } else {
      localStorage.removeItem(COMPANION_AUTO_MODEL_KEY);
    }
  }, [autoModel]);

  useEffect(() => {
    if (mode === "create" || !companionId) {
      setGalleryImages([]);
      setGalleryLoading(false);
      return;
    }

    let alive = true;
    setGalleryLoading(true);
    listCompanionImages(companionId)
      .then((images) => {
        if (alive) setGalleryImages(images);
      })
      .catch((e: unknown) => {
        if (alive) setError(getErrorDetail(e));
      })
      .finally(() => {
        if (alive) setGalleryLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [companionId, mode]);

  useEffect(() => {
    if (!saved) return;
    clearSavedRef.current = setTimeout(() => setSaved(false), 2000);
    return () => {
      if (clearSavedRef.current) clearTimeout(clearSavedRef.current);
    };
  }, [saved]);

  const set = <K extends keyof CompanionFormData>(
    key: K,
    value: CompanionFormData[K],
  ) => setForm((current) => ({ ...current, [key]: value }));

  const buildAutoContext = () => ({
    gender: form.gender,
    name: form.name.trim() || null,
    backstory: form.backstory.trim() || null,
    persona: form.persona.trim() || null,
    personality: {
      humor_style: form.humor_style,
      communication_style: form.communication_style,
      sliders: form.personality_sliders,
    },
  });

  const handleAutoGenerate = async (field: GenerateCompanionFieldName) => {
    setAutoLoadingField(field);
    setError(null);
    try {
      const response = await generateCompanionField({
        field,
        context: buildAutoContext(),
        ...companionAutoRouting(autoProvider, autoModel),
      });

      if (field === "personality") {
        const content = normalizeGeneratedPersonality(response.content);
        setForm((current) => ({
          ...current,
          humor_style: content.humor_style,
          communication_style: content.communication_style,
          personality_sliders: PERSONALITY_DIMS.reduce<Record<string, number>>(
            (next, dim) => {
              next[dim] = content.sliders[dim];
              return next;
            },
            { ...current.personality_sliders },
          ),
        }));
      } else if (typeof response.content === "string") {
        const value = response.content.trim();
        if (field === "name") set("name", value);
        if (field === "backstory") set("backstory", value);
        if (field === "persona") set("persona", value);
        if (field === "favorite_topics") {
          set("favorite_topics", value.split(",").map((topic) => topic.trim()).filter(Boolean));
        }
      }
    } catch (e: unknown) {
      setError(getErrorDetail(e));
    } finally {
      setAutoLoadingField(null);
    }
  };

  const buildPayload = (avatarUrl = form.avatar_url): CompanionCreate => ({
    name: form.name.trim() || "Companion",
    gender: form.gender,
    persona: form.persona.trim() || null,
    avatar_header: form.avatar_header.trim() || null,
    description: galleryPrompt,
    avatar_url: avatarUrl,
    backstory: form.backstory.trim() || null,
    personality_sliders: form.personality_sliders,
    humor_style: form.humor_style || null,
    communication_style: form.communication_style || null,
    punctuation_habits: parseJsonObjectField(
      "Punctuation habits",
      form.punctuation_habits,
    ),
    sentence_length_profile: parseJsonObjectField(
      "Sentence length profile",
      form.sentence_length_profile,
    ),
    emoji_style: parseJsonObjectField("Emoji style", form.emoji_style),
    vocabulary_density: form.vocabulary_density,
    slang_profile: parseJsonObjectField("Slang profile", form.slang_profile),
    adaptive_verbosity: form.adaptive_verbosity,
    favorite_topics: form.favorite_topics,
    voice_id: voiceIdForValue(form.voice_id),
  });

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const savedCompanion =
        mode === "edit" && companionId
          ? await updateCompanion(companionId, buildPayload())
          : await createCompanion(buildPayload());
      setForm(companionToForm(savedCompanion));
      setSaved(true);
      onSave?.(savedCompanion);
    } catch (e: unknown) {
      setError(getErrorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  const saveAvatarUrl = async (avatarUrl: string) => {
    if (mode === "create" || !companionId) {
      set("avatar_url", avatarUrl);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const updated = await updateCompanion(companionId, buildPayload(avatarUrl));
      setForm(companionToForm(updated));
      setSaved(true);
    } catch (e: unknown) {
      setError(getErrorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  const handleAvatarUpload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onloadend = async () => {
      if (typeof reader.result !== "string") return;
      setSaving(true);
      setError(null);
      try {
        const result = await uploadVisualImage({
          image_base64: reader.result,
          ...(companionId ? { companion_id: companionId } : {}),
          prompt_used: avatarImagePrompt(),
        });
        const uploadedImage: GeneratedCompanionImage = {
          image_id: result.image_id,
          image_url: result.image_url,
          mime_type: file.type || "image/png",
          prompt_used: avatarImagePrompt(),
          model_used: null,
          width: null,
          height: null,
          created_at: new Date().toISOString(),
        };
        setGalleryImages((images) => [uploadedImage, ...images]);
        setActiveGalleryImageId(result.image_id);
        await saveAvatarUrl(result.image_url);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to upload avatar image.");
      } finally {
        setSaving(false);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleGenerateGalleryImage = async () => {
    const prompt = avatarImagePrompt();
    if (!prompt) return;

    setGeneratingGalleryImage(true);
    setError(null);
    try {
      const generated = await generateVisualImage({
        prompt,
        // Match AVTR's output frame; the worker also fits older images without stretching.
        width: 1280,
        height: 720,
        ...(companionId ? { companion_id: companionId } : {}),
      });
      const imageUrl = generatedImageUrl(generated);
      const galleryImage = generatedImageToGalleryItem(generated, imageUrl);
      setGalleryImages((images) => [
        galleryImage,
        ...images,
      ]);
      setActiveGalleryImageId(galleryImage.image_id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to generate image.");
    } finally {
      setGeneratingGalleryImage(false);
    }
  };

  const handleFillGalleryPromptFromProfile = () => {
    const subject = form.name.trim() || "the companion";
    const sentences = [
      `Portrait of ${subject}, a ${form.gender} companion.`,
      firstSentences(form.persona),
      firstSentences(form.backstory),
      `${personalityHighlights(form.personality_sliders)}, with ${fmtLabel(form.humor_style)} humor and a ${fmtLabel(form.communication_style)} communication style.`,
      form.favorite_topics.length > 0
        ? `Interests include ${form.favorite_topics.join(", ")}.`
        : "",
    ].filter(Boolean);
    const addition = sentences.join(" ");
    setGalleryPrompt((current) =>
      current.trim() ? `${current.trim()}\n\n${addition}` : addition,
    );
  };

  const handleSelectGalleryImage = async (image: GeneratedCompanionImage) => {
    setActiveGalleryImageId(image.image_id);
    await saveAvatarUrl(image.image_url);
  };

  const handleSaveImageDescription = async () => {
    if (!activeGalleryImageId || activeGalleryImageId.startsWith("generated-")) return;

    setSavingImageDescription(true);
    setError(null);
    try {
      const updated = await updateVisualImageDescription(
        activeGalleryImageId,
        avatarImagePrompt(),
      );
      setGalleryImages((images) =>
        images.map((image) =>
          image.image_id === activeGalleryImageId
            ? { ...image, prompt_used: updated.prompt_used ?? "" }
            : image,
        ),
      );
      setSaved(true);
    } catch (e: unknown) {
      setError(getErrorDetail(e));
    } finally {
      setSavingImageDescription(false);
    }
  };

  const handleDeleteGalleryImage = async (imageId: string) => {
    if (imageId.startsWith("generated-")) {
      setGalleryImages((images) => images.filter((image) => image.image_id !== imageId));
      if (activeGalleryImageId === imageId) setActiveGalleryImageId(null);
      return;
    }

    setError(null);
    try {
      await deleteVisualImage(imageId);
      setGalleryImages((images) => images.filter((image) => image.image_id !== imageId));
      if (activeGalleryImageId === imageId) setActiveGalleryImageId(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete image.");
    }
  };

  const addTopic = () => {
    const topic = topicInput.trim();
    if (!topic || form.favorite_topics.includes(topic)) return;
    set("favorite_topics", [...form.favorite_topics, topic]);
    setTopicInput("");
  };

  const removeTopic = (topic: string) => {
    set(
      "favorite_topics",
      form.favorite_topics.filter((current) => current !== topic),
    );
  };

  const disabled = saving || generatingGalleryImage || savingImageDescription;

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <form className="flex flex-col gap-5" onSubmit={handleSubmit}>
        {error ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2">
          <label className={LABEL}>
            Gender
            <div className="grid grid-cols-2 gap-2 rounded-lg border border-gray-700 bg-gray-900 p-1">
              {(["female", "male"] as const).map((gender) => (
                <label
                  key={gender}
                  className={[
                    "flex cursor-pointer items-center justify-center rounded-md px-3 py-2 text-sm font-medium capitalize transition",
                    form.gender === gender
                      ? "bg-indigo-600 text-white shadow"
                      : "text-gray-300 hover:bg-gray-800 hover:text-gray-100",
                    disabled ? "cursor-not-allowed opacity-60" : "",
                  ].join(" ")}
                >
                  <input
                    type="radio"
                    name={`companion-${mode}-gender`}
                    value={gender}
                    checked={form.gender === gender}
                    disabled={disabled}
                    className="sr-only"
                    onChange={() => set("gender", gender)}
                  />
                  {gender}
                </label>
              ))}
            </div>
          </label>

          <label className={LABEL}>
            Auto-generate model
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="flex min-w-0 flex-col gap-1">
                <span className="text-xs text-gray-400">Provider</span>
                <input
                  type="text"
                  list="companion-auto-provider-options"
                  value={autoProvider}
                  disabled={disabled}
                  className={INPUT}
                  placeholder="Default"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setAutoProvider(e.target.value)
                  }
                />
                <datalist id="companion-auto-provider-options">
                  {AUTO_PROVIDER_OPTIONS.map((provider) => (
                    <option key={provider} value={provider} />
                  ))}
                </datalist>
              </div>
              <div className="flex min-w-0 flex-col gap-1">
                <span className="text-xs text-gray-400">Model</span>
                <input
                  type="text"
                  list="companion-auto-model-options"
                  value={autoModel}
                  disabled={disabled || isAutoModelDisabled}
                  className={INPUT}
                  placeholder={isAutoModelDisabled ? "Provider default" : "Default"}
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setAutoModel(e.target.value)
                  }
                />
                <datalist id="companion-auto-model-options">
                  {AUTO_MODEL_OPTIONS.map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              </div>
            </div>
          </label>
        </div>

        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          {form.avatar_url ? (
            <img
              src={form.avatar_url}
              alt="Avatar preview"
              className="h-20 w-20 rounded-full object-cover ring-2 ring-gray-600 shadow-md"
            />
          ) : (
            <div
              className="flex h-20 w-20 items-center justify-center rounded-full text-2xl font-bold text-white ring-2 ring-indigo-500/50 shadow-md"
              style={{ backgroundColor: avatarColorForKey(companionId || form.name || "?") }}
            >
              {getInitial(form.name || "?")}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <label
              htmlFor={`avatar-upload-input-${mode}`}
              className={`cursor-pointer rounded-lg bg-gray-700 px-4 py-2 text-sm font-medium text-gray-200 transition hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
            >
              Upload Avatar
              <input
                id={`avatar-upload-input-${mode}`}
                type="file"
                accept="image/*"
                className="hidden"
                disabled={disabled}
                onChange={handleAvatarUpload}
              />
            </label>
            {form.avatar_url ? (
              <button
                type="button"
                disabled={disabled}
                onClick={() => set("avatar_url", null)}
                className="rounded-lg border border-red-800 bg-red-950/40 px-4 py-2 text-sm font-medium text-red-200 transition hover:bg-red-900/40 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
              >
                Remove
              </button>
            ) : null}
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded-lg border border-gray-700 bg-gray-900/50 p-4">
          <div className="flex flex-col gap-1">
            <h4 className="text-sm font-semibold text-gray-100">Avatar Gallery</h4>
            <p className="text-xs text-gray-400">
              Generate companion images and choose one as the avatar. For best results with the animated avatar, use a 16:9 aspect ratio with a recommended resolution of 1280 × 720 pixels when generating or uploading images.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <label className={LABEL}>
              Avatar header
              <input
                type="text"
                value={form.avatar_header}
                disabled={disabled}
                className={INPUT}
                maxLength={255}
                placeholder="Short avatar label, e.g. Beautiful Blonde"
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  set("avatar_header", e.target.value)
                }
              />
            </label>
            <label className={LABEL}>
              Avatar description
              <textarea
                rows={5}
                value={galleryPrompt}
                disabled={disabled}
                className={`min-h-28 resize-y ${INPUT}`}
                placeholder="Describe details to use when generating avatar images"
                onChange={(e) => setGalleryPrompt(e.target.value)}
              />
            </label>
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                disabled={disabled || !avatarImagePrompt()}
                onClick={handleGenerateGalleryImage}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
              >
                {generatingGalleryImage ? "Generating..." : "Generate"}
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={handleFillGalleryPromptFromProfile}
                className="rounded-lg bg-gray-700 px-4 py-2 text-sm font-medium text-gray-200 transition hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Fill from profile
              </button>
              <button
                type="button"
                disabled={disabled || (!form.avatar_header && !galleryPrompt)}
                onClick={() => {
                  set("avatar_header", "");
                  setGalleryPrompt("");
                }}
                className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Clear
              </button>
            </div>
            {activeGalleryImageId ? (
              <div className="flex justify-end">
                <button
                  type="button"
                  disabled={disabled}
                  onClick={handleSaveImageDescription}
                  className="text-xs font-medium text-cyan-400 transition hover:text-cyan-300 hover:underline focus:outline-none focus:ring-2 focus:ring-cyan-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {savingImageDescription ? "Saving description..." : "Save description"}
                </button>
              </div>
            ) : null}
          </div>
          {galleryLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-gray-500 border-t-gray-200" />
              Loading generated images...
            </div>
          ) : galleryImages.length > 0 ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              {galleryImages.map((image) => (
                <div
                  key={image.image_id}
                  className="relative overflow-hidden rounded-lg border border-gray-700 bg-gray-950"
                >
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => handleSelectGalleryImage(image)}
                    className="block w-full focus:outline-none focus:ring-2 focus:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-70"
                  >
                    <img
                      src={image.image_url}
                      alt={imageDescription(image) || "Companion image"}
                      className="aspect-square w-full object-cover"
                    />
                  </button>
                  <button
                    type="button"
                    title="Delete image"
                    onClick={() => handleDeleteGalleryImage(image.image_id)}
                    className="absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-black/60 text-white transition-colors hover:bg-black/80 focus:outline-none focus:ring-2 focus:ring-red-500"
                  >
                    <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                  <div className="flex flex-col gap-2 p-2">
                    <p className="line-clamp-2 min-h-8 text-xs text-gray-400">
                      {galleryFooterText(image)}
                    </p>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => handleSelectGalleryImage(image)}
                      className="rounded-md bg-gray-700 px-3 py-1.5 text-xs font-medium text-gray-100 transition hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      Set as Avatar
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400">No generated images for this companion yet.</p>
          )}
        </div>

        <label className={LABEL}>
          <FieldTitle field="name" loadingField={autoLoadingField} disabled={disabled || autoLoadingField !== null} onAuto={handleAutoGenerate}>
            Name
          </FieldTitle>
          <input
            type="text"
            required
            value={form.name}
            disabled={disabled}
            className={INPUT}
            placeholder="Companion name"
            onChange={(e: ChangeEvent<HTMLInputElement>) => set("name", e.target.value)}
          />
        </label>

        <label className={LABEL}>
          Default Voice
          <select
            aria-label="Default Voice"
            value={form.voice_id}
            disabled={disabled}
            className={INPUT}
            onChange={(e: ChangeEvent<HTMLSelectElement>) => set("voice_id", e.target.value)}
          >
            {companionVoiceOptions(voices).map((option) => (
              <option key={option.value || "builtin"} value={option.value}>
                {option.label}
              </option>
            ))}
            {form.voice_id && !voices.some((voice) => voice.voice_id === form.voice_id) ? (
              <option value={form.voice_id}>Loading voice…</option>
            ) : null}
          </select>
          <span className="text-xs font-normal text-gray-500">
            Who speaks for this companion. A conversation can still choose another voice. Manage voices in
            the Voice Library.
          </span>
        </label>

        <label className={LABEL}>
          <FieldTitle field="backstory" loadingField={autoLoadingField} disabled={disabled || autoLoadingField !== null} onAuto={handleAutoGenerate}>
            Backstory
          </FieldTitle>
          <textarea
            rows={4}
            value={form.backstory}
            disabled={disabled}
            className={`resize-y ${INPUT}`}
            placeholder="Background, history, and defining context"
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) => set("backstory", e.target.value)}
          />
        </label>

        <label className={LABEL}>
          <FieldTitle field="persona" loadingField={autoLoadingField} disabled={disabled || autoLoadingField !== null} onAuto={handleAutoGenerate}>
            Persona
          </FieldTitle>
          <textarea
            rows={4}
            value={form.persona}
            disabled={disabled}
            className={`resize-y ${INPUT}`}
            placeholder="Free-text persona prompt - how this companion presents itself"
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) => set("persona", e.target.value)}
          />
        </label>

        <p className="pt-1 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Personality
          <FieldTitle field="personality" loadingField={autoLoadingField} disabled={disabled || autoLoadingField !== null} onAuto={handleAutoGenerate}>
            {""}
          </FieldTitle>
        </p>

        <div className="grid gap-4 md:grid-cols-2">
          <label className={LABEL}>
            Humor style
            <select
              value={form.humor_style}
              disabled={disabled}
              className={`capitalize ${INPUT}`}
              onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                set("humor_style", e.target.value)
              }
            >
              {HUMOR_STYLES.map((style) => (
                <option key={style} value={style}>
                  {fmtLabel(style)}
                </option>
              ))}
            </select>
          </label>

          <label className={LABEL}>
            Communication style
            <select
              value={form.communication_style}
              disabled={disabled}
              className={`capitalize ${INPUT}`}
              onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                set("communication_style", e.target.value)
              }
            >
              {COMM_STYLES.map((style) => (
                <option key={style} value={style}>
                  {fmtLabel(style)}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium text-gray-200">Personality sliders</p>
          <div className="grid gap-4 md:grid-cols-2">
            {PERSONALITY_DIMS.map((dim) => {
              const value = form.personality_sliders[dim] ?? DEFAULT_SLIDERS[dim];
              return (
                <label key={dim} className={LABEL}>
                  <span className="flex items-center justify-between">
                    <span className="capitalize">{fmtLabel(dim)}</span>
                    <span className="font-mono text-xs text-gray-400">{value.toFixed(2)}</span>
                  </span>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={value}
                    disabled={disabled}
                    className="h-2 w-full cursor-pointer accent-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      set("personality_sliders", {
                        ...form.personality_sliders,
                        [dim]: Number(e.target.value),
                      })
                    }
                  />
                </label>
              );
            })}
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <label className="text-sm font-medium text-gray-200" htmlFor={`favorite-topic-${mode}`}>
            <FieldTitle field="favorite_topics" loadingField={autoLoadingField} disabled={disabled || autoLoadingField !== null} onAuto={handleAutoGenerate}>
              Favorite topics
            </FieldTitle>
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              id={`favorite-topic-${mode}`}
              type="text"
              value={topicInput}
              disabled={disabled}
              className={`min-w-0 flex-1 ${INPUT}`}
              placeholder="Add a topic"
              onChange={(e) => setTopicInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addTopic();
                }
              }}
            />
            <button
              type="button"
              disabled={disabled || !topicInput.trim()}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
              onClick={addTopic}
            >
              Add
            </button>
          </div>
          {form.favorite_topics.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {form.favorite_topics.map((topic) => (
                <span
                  key={topic}
                  className="inline-flex items-center gap-2 rounded-full border border-gray-600 bg-gray-700 px-3 py-1 text-sm text-gray-100"
                >
                  {topic}
                  <button
                    type="button"
                    disabled={disabled}
                    className="text-gray-400 transition hover:text-gray-100 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
                    aria-label={`Remove ${topic}`}
                    onClick={() => removeTopic(topic)}
                  >
                    x
                  </button>
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium text-gray-200">Linguistic fingerprint</p>
          <div className="grid gap-4 md:grid-cols-2">
            {FINGERPRINT_FIELDS.map((field) => (
              <label key={field.key} className={LABEL}>
                {field.label}
                <textarea
                  rows={4}
                  value={form[field.key]}
                  disabled={disabled}
                  className={`resize-y font-mono ${INPUT}`}
                  placeholder={field.placeholder}
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                    set(field.key, e.target.value)
                  }
                />
              </label>
            ))}

            <label className={LABEL}>
              Vocabulary density
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={form.vocabulary_density ?? ""}
                disabled={disabled}
                className={INPUT}
                placeholder="0.50"
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  set("vocabulary_density", e.target.value === "" ? null : Number(e.target.value))
                }
              />
            </label>

            <label className={LABEL}>
              Adaptive verbosity
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={form.adaptive_verbosity ?? ""}
                disabled={disabled}
                className={INPUT}
                placeholder="0.50"
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  set("adaptive_verbosity", e.target.value === "" ? null : Number(e.target.value))
                }
              />
            </label>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3">
          {saved ? <span className="text-sm font-medium text-green-400">Saved!</span> : null}
          {showCancel ? (
            <button
              type="button"
              disabled={disabled}
              className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={onCancel}
            >
              Cancel
            </button>
          ) : null}
          <button
            type="submit"
            disabled={disabled || !form.name.trim()}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
          >
            {saving ? "Saving..." : mode === "create" ? "Create" : "Save"}
          </button>
        </div>
      </form>
    </section>
  );
}

export default CompanionForm;
