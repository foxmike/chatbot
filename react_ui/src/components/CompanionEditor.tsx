import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  generateVisualImage,
  listCompanionImages,
  updateCompanion,
  uploadVisualImage,
} from "../api/client";
import type {
  Companion,
  GeneratedCompanionImage,
  GenerateVisualImageResponse,
} from "../api/client";
import type { ApiError } from "../types";
import { avatarColorForKey, getInitial } from "../utils/avatar";

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

const DEFAULT_SLIDERS: Record<string, number> = {
  openness: 0.7,
  conscientiousness: 0.6,
  extraversion: 0.5,
  agreeableness: 0.8,
  neuroticism: 0.3,
  curiosity: 0.7,
  creativity: 0.6,
};

interface EditorForm {
  name: string;
  persona: string;
  backstory: string;
  personality_sliders: Record<string, number>;
  humor_style: string;
  communication_style: string;
  favorite_topics: string[];
  avatar_url: string | null;
}

function companionToForm(c: Companion): EditorForm {
  return {
    name: c.name,
    persona: c.persona ?? "",
    backstory: c.backstory ?? "",
    personality_sliders: c.personality_sliders
      ? { ...DEFAULT_SLIDERS, ...c.personality_sliders }
      : { ...DEFAULT_SLIDERS },
    humor_style: c.humor_style ?? "witty",
    communication_style: c.communication_style ?? "concise",
    favorite_topics: c.favorite_topics ?? [],
    avatar_url: c.avatar_url ?? null,
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

function fmtLabel(v: string) {
  return v.replace(/_/g, " ");
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

interface CompanionEditorProps {
  companion: Companion;
  onSave?: (updated: Companion) => void;
  showCancel?: boolean;
  onCancel?: () => void;
}

const INPUT =
  "rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60";
const LABEL = "flex flex-col gap-2 text-sm font-medium text-gray-200";

export function CompanionEditor({
  companion,
  onSave,
  showCancel = false,
  onCancel,
}: CompanionEditorProps) {
  const companionId = companion.companion_id;
  const [form, setForm] = useState<EditorForm>(() => companionToForm(companion));
  const [topicInput, setTopicInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [generatingGalleryImage, setGeneratingGalleryImage] = useState(false);
  const [galleryLoading, setGalleryLoading] = useState(true);
  const [galleryPrompt, setGalleryPrompt] = useState("");
  const [galleryImages, setGalleryImages] = useState<GeneratedCompanionImage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const clearSavedRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleAvatarUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onloadend = async () => {
      if (typeof reader.result === "string") {
        setSaving(true);
        setError(null);
        try {
          const res = await uploadVisualImage({
            image_base64: reader.result,
            companion_id: companionId,
          });
          set("avatar_url", res.image_url);
        } catch (err: unknown) {
          setError(err instanceof Error ? err.message : "Failed to upload avatar image.");
        } finally {
          setSaving(false);
        }
      }
    };
    reader.readAsDataURL(file);
  };

  const handleRegenerateAvatar = async () => {
    setRegenerating(true);
    setError(null);
    try {
      const generated = await generateVisualImage({
        prompt: `Avatar portrait of ${form.name || "Companion"}`,
        companion_id: companionId,
      });
      const imageUrl = generatedImageUrl(generated);
      set("avatar_url", imageUrl);
      setGalleryImages((images) => [
        generatedImageToGalleryItem(generated, imageUrl),
        ...images,
      ]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to regenerate avatar.");
    } finally {
      setRegenerating(false);
    }
  };

  useEffect(() => {
    setError(null);
    setSaved(false);
    setTopicInput("");
    setForm(companionToForm(companion));
  }, [companion]);

  useEffect(() => {
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
  }, [companionId]);

  useEffect(() => {
    if (!saved) return;
    clearSavedRef.current = setTimeout(() => setSaved(false), 2000);
    return () => {
      if (clearSavedRef.current) clearTimeout(clearSavedRef.current);
    };
  }, [saved]);

  const set = <K extends keyof EditorForm>(key: K, value: EditorForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const addTopic = () => {
    const t = topicInput.trim();
    if (!t || form.favorite_topics.includes(t)) return;
    set("favorite_topics", [...form.favorite_topics, t]);
    setTopicInput("");
  };

  const removeTopic = (t: string) =>
    set(
      "favorite_topics",
      form.favorite_topics.filter((x) => x !== t),
    );

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const updated = await updateCompanion(companionId, {
        name: form.name.trim() || "Companion",
        persona: form.persona.trim() || null,
        description: null,
        avatar_url: form.avatar_url,
        backstory: form.backstory.trim() || null,
        personality_sliders: form.personality_sliders,
        humor_style: form.humor_style || null,
        communication_style: form.communication_style || null,
        favorite_topics: form.favorite_topics,
      });
      setForm(companionToForm(updated));
      setSaved(true);
      onSave?.(updated);
    } catch (e: unknown) {
      setError(getErrorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  const saveAvatarUrl = async (avatarUrl: string) => {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateCompanion(companionId, {
        name: form.name.trim() || "Companion",
        persona: form.persona.trim() || null,
        description: null,
        avatar_url: avatarUrl,
        backstory: form.backstory.trim() || null,
        personality_sliders: form.personality_sliders,
        humor_style: form.humor_style || null,
        communication_style: form.communication_style || null,
        favorite_topics: form.favorite_topics,
      });
      setForm(companionToForm(updated));
      setSaved(true);
      onSave?.(updated);
    } catch (e: unknown) {
      setError(getErrorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  const handleGenerateGalleryImage = async () => {
    const prompt = galleryPrompt.trim();
    if (!prompt) return;

    setGeneratingGalleryImage(true);
    setError(null);
    try {
      const generated = await generateVisualImage({
        prompt,
        companion_id: companionId,
      });
      const imageUrl = generatedImageUrl(generated);
      setGalleryImages((images) => [
        generatedImageToGalleryItem(generated, imageUrl),
        ...images,
      ]);
      setGalleryPrompt("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to generate image.");
    } finally {
      setGeneratingGalleryImage(false);
    }
  };

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <form className="flex flex-col gap-5" onSubmit={handleSubmit}>
        {/* ── Identity ───────────────────────────────────────────────── */}
        <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
          Identity
        </h3>

        {error && (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        )}

        {/* ── Avatar Upload & Regenerate ────────────────────────────── */}
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
              style={{
                backgroundColor: avatarColorForKey(companionId || form.name),
              }}
            >
              {getInitial(form.name)}
            </div>
          )}
          
          <div className="flex flex-wrap gap-2">
            <label
              htmlFor="avatar-upload-input"
              className={`cursor-pointer rounded-lg bg-gray-700 px-4 py-2 text-sm font-medium text-gray-200 transition hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 ${(saving || regenerating) ? "opacity-50 cursor-not-allowed" : ""}`}
            >
              Upload Avatar
              <input
                id="avatar-upload-input"
                type="file"
                accept="image/*"
                className="hidden"
                disabled={saving || regenerating}
                onChange={handleAvatarUpload}
              />
            </label>

            <button
              type="button"
              disabled={saving || regenerating}
              onClick={handleRegenerateAvatar}
              className="rounded-lg bg-gray-700 px-4 py-2 text-sm font-medium text-gray-200 transition hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {regenerating ? "Regenerating…" : "Regenerate Avatar"}
            </button>
            
            {form.avatar_url && (
              <button
                type="button"
                disabled={saving || regenerating}
                onClick={() => set("avatar_url", null)}
                className="rounded-lg bg-red-950/40 border border-red-800 px-4 py-2 text-sm font-medium text-red-200 transition hover:bg-red-900/40 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
              >
                Remove
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded-lg border border-gray-700 bg-gray-900/50 p-4">
          <div className="flex flex-col gap-1">
            <h4 className="text-sm font-semibold text-gray-100">
              Avatar Gallery
            </h4>
            <p className="text-xs text-gray-400">
              Generate companion images and choose one as the avatar.
            </p>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              value={galleryPrompt}
              disabled={saving || generatingGalleryImage}
              className={`min-w-0 flex-1 ${INPUT}`}
              placeholder="Describe an avatar image"
              onChange={(e) => setGalleryPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleGenerateGalleryImage();
                }
              }}
            />
            <button
              type="button"
              disabled={
                saving || generatingGalleryImage || !galleryPrompt.trim()
              }
              onClick={handleGenerateGalleryImage}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
            >
              {generatingGalleryImage && (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              )}
              {generatingGalleryImage ? "Generating..." : "Generate"}
            </button>
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
                  className="overflow-hidden rounded-lg border border-gray-700 bg-gray-950"
                >
                  <img
                    src={image.image_url}
                    alt={image.prompt_used ?? "Generated companion image"}
                    className="aspect-square w-full object-cover"
                  />
                  <div className="flex flex-col gap-2 p-2">
                    <p className="line-clamp-2 min-h-8 text-xs text-gray-400">
                      {image.prompt_used ?? "Generated avatar"}
                    </p>
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() => saveAvatarUrl(image.image_url)}
                      className="rounded-md bg-gray-700 px-3 py-1.5 text-xs font-medium text-gray-100 transition hover:bg-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      Set as Avatar
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400">
              No generated images for this companion yet.
            </p>
          )}
        </div>

        <label className={LABEL}>
          Name
          <input
            type="text"
            required
            value={form.name}
            disabled={saving}
            className={INPUT}
            placeholder="Companion name"
            onChange={(e: ChangeEvent<HTMLInputElement>) =>
              set("name", e.target.value)
            }
          />
        </label>

        <label className={LABEL}>
          Backstory
          <textarea
            rows={4}
            value={form.backstory}
            disabled={saving}
            className={`resize-y ${INPUT}`}
            placeholder="Background, history, and defining context"
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
              set("backstory", e.target.value)
            }
          />
        </label>

        <label className={LABEL}>
          Persona
          <textarea
            rows={4}
            value={form.persona}
            disabled={saving}
            className={`resize-y ${INPUT}`}
            placeholder="Free-text persona prompt — how this companion presents itself"
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
              set("persona", e.target.value)
            }
          />
        </label>

        {/* ── Personality ────────────────────────────────────────────── */}
        <h3 className="pt-1 text-sm font-semibold uppercase tracking-wide text-gray-400">
          Personality
        </h3>

        <div className="grid gap-4 md:grid-cols-2">
          <label className={LABEL}>
            Humor style
            <select
              value={form.humor_style}
              disabled={saving}
              className={`capitalize ${INPUT}`}
              onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                set("humor_style", e.target.value)
              }
            >
              {HUMOR_STYLES.map((s) => (
                <option key={s} value={s}>
                  {fmtLabel(s)}
                </option>
              ))}
            </select>
          </label>

          <label className={LABEL}>
            Communication style
            <select
              value={form.communication_style}
              disabled={saving}
              className={`capitalize ${INPUT}`}
              onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                set("communication_style", e.target.value)
              }
            >
              {COMM_STYLES.map((s) => (
                <option key={s} value={s}>
                  {fmtLabel(s)}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium text-gray-200">
            Personality sliders
          </p>
          <div className="grid gap-4 md:grid-cols-2">
            {PERSONALITY_DIMS.map((dim) => {
              const val = form.personality_sliders[dim] ?? 0.5;
              return (
                <label key={dim} className={LABEL}>
                  <span className="flex items-center justify-between">
                    <span className="capitalize">{fmtLabel(dim)}</span>
                    <span className="font-mono text-xs text-gray-400">
                      {val.toFixed(2)}
                    </span>
                  </span>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={val}
                    disabled={saving}
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
          <label
            className="text-sm font-medium text-gray-200"
            htmlFor="ce-topic"
          >
            Favorite topics
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              id="ce-topic"
              type="text"
              value={topicInput}
              disabled={saving}
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
              disabled={saving || !topicInput.trim()}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
              onClick={addTopic}
            >
              Add
            </button>
          </div>
          {form.favorite_topics.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {form.favorite_topics.map((t) => (
                <span
                  key={t}
                  className="inline-flex items-center gap-2 rounded-full border border-gray-600 bg-gray-700 px-3 py-1 text-sm text-gray-100"
                >
                  {t}
                  <button
                    type="button"
                    disabled={saving}
                    className="text-gray-400 transition hover:text-gray-100 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
                    aria-label={`Remove ${t}`}
                    onClick={() => removeTopic(t)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* ── Save row ───────────────────────────────────────────────── */}
        <div className="flex items-center justify-end gap-3">
          {saved && (
            <span className="text-sm font-medium text-green-400">Saved!</span>
          )}
          {showCancel && (
            <button
              type="button"
              disabled={saving}
              className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={onCancel}
            >
              Cancel
            </button>
          )}
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </section>
  );
}

export default CompanionEditor;
