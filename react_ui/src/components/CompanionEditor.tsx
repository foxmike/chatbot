import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { getCompanion, updateCompanion, getAccessToken } from "../api/client";
import type { Companion } from "../api/client";
import type { ApiError } from "../types";

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

const emptyForm: EditorForm = {
  name: "",
  persona: "",
  backstory: "",
  personality_sliders: { ...DEFAULT_SLIDERS },
  humor_style: "witty",
  communication_style: "concise",
  favorite_topics: [],
  avatar_url: null,
};

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

interface CompanionEditorProps {
  companionId: string;
  onSave?: (updated: Companion) => void;
}

const INPUT =
  "rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60";
const LABEL = "flex flex-col gap-2 text-sm font-medium text-gray-200";

export function CompanionEditor({ companionId, onSave }: CompanionEditorProps) {
  const [form, setForm] = useState<EditorForm>(emptyForm);
  const [topicInput, setTopicInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const clearSavedRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleAvatarUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onloadend = () => {
      if (typeof reader.result === "string") {
        set("avatar_url", reader.result);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleRegenerateAvatar = async () => {
    setRegenerating(true);
    setError(null);
    try {
      const token = getAccessToken();
      const response = await fetch("/api/visual/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          prompt: `Avatar portrait of ${form.name || "Companion"}`,
        }),
      });

      if (!response.ok) {
        let errMsg = `Failed to generate avatar: ${response.statusText}`;
        try {
          const body = await response.json();
          if (body && typeof body.detail === "string") {
            errMsg = body.detail;
          }
        } catch {
          // ignore
        }
        throw new Error(errMsg);
      }

      const data = await response.json();
      const dataUrl = `data:${data.mime_type};base64,${data.image_base64}`;
      set("avatar_url", dataUrl);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to regenerate avatar.");
    } finally {
      setRegenerating(false);
    }
  };

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setSaved(false);
    getCompanion(companionId)
      .then((c) => {
        if (alive) setForm(companionToForm(c));
      })
      .catch((e: unknown) => {
        if (alive) setError(getErrorDetail(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
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

  if (loading) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-sm text-gray-400">
        Loading companion…
      </div>
    );
  }

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
            <div className="flex h-20 w-20 items-center justify-center rounded-full bg-indigo-700 text-2xl font-bold text-white ring-2 ring-indigo-500/50 shadow-md">
              {form.name ? form.name.charAt(0).toUpperCase() : "?"}
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
