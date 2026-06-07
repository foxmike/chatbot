import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  clearAccessToken,
  createCompanion,
  deleteCompanion,
  listCompanions,
  updateCompanion,
} from "../api/client";
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

interface CompanionSelectorProps {
  onSelectCompanion: (companion: Companion) => void;
  onLogout: () => void;
}

function getErrorDetail(error: unknown) {
  if (
    typeof error === "object" &&
    error !== null &&
    "detail" in error &&
    typeof (error as ApiError).detail === "string"
  ) {
    return (error as ApiError).detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Something went wrong. Please try again.";
}

function fmtLabel(v: string) {
  return v.replace(/_/g, " ");
}

const AVATAR_COLORS = [
  "#4f46e5",
  "#0891b2",
  "#059669",
  "#7c3aed",
  "#db2777",
  "#ea580c",
  "#ca8a04",
  "#0d9488",
  "#2563eb",
  "#9333ea",
];

function avatarColorForName(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function companionInitial(name: string) {
  return name.trim().charAt(0).toUpperCase() || "?";
}

const INPUT =
  "rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60";
const LABEL = "flex flex-col gap-2 text-sm font-medium text-gray-200";

function CompanionCardAvatar({ companion }: { companion: Companion }) {
  if (companion.avatar_url) {
    return (
      <img
        src={companion.avatar_url}
        alt={`${companion.name} avatar`}
        className="h-16 w-16 shrink-0 rounded-full object-cover ring-2 ring-gray-700"
      />
    );
  }

  return (
    <div
      className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full text-2xl font-semibold text-white ring-2 ring-gray-700"
      style={{ backgroundColor: avatarColorForName(companion.name) }}
    >
      {companionInitial(companion.name)}
    </div>
  );
}

export function CompanionSelector({
  onSelectCompanion,
  onLogout,
}: CompanionSelectorProps) {
  const [companions, setCompanions] = useState<Companion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ── Create form state ──────────────────────────────────────────────
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createPersona, setCreatePersona] = useState("");
  const [createBackstory, setCreateBackstory] = useState("");
  const [createSliders, setCreateSliders] = useState<Record<string, number>>(
    { ...DEFAULT_SLIDERS },
  );
  const [createHumorStyle, setCreateHumorStyle] = useState<string>("witty");
  const [createCommStyle, setCreateCommStyle] = useState<string>("concise");
  const [createTopicsInput, setCreateTopicsInput] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // ── Delete / modal-edit state ─────────────────────────────────────
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingCompanion, setEditingCompanion] = useState<Companion | null>(
    null,
  );
  const [editName, setEditName] = useState("");
  const [editBackstory, setEditBackstory] = useState("");
  const [editPersona, setEditPersona] = useState("");
  const [editSliders, setEditSliders] = useState<Record<string, number>>({
    ...DEFAULT_SLIDERS,
  });
  const [editHumorStyle, setEditHumorStyle] = useState<string>("witty");
  const [editCommStyle, setEditCommStyle] = useState<string>("concise");
  const [editTopicsInput, setEditTopicsInput] = useState("");
  const [isSavingEdit, setIsSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const loadCompanions = async () => {
    setLoading(true);
    setError(null);

    try {
      const result = await listCompanions();
      setCompanions(result);
    } catch (err) {
      console.error("Failed to load companions", err);
      setError(getErrorDetail(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCompanions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Parse comma-separated topics string into cleaned array
  const parseTopics = (raw: string): string[] =>
    raw
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

  const resetCreateForm = () => {
    setCreateName("");
    setCreatePersona("");
    setCreateBackstory("");
    setCreateSliders({ ...DEFAULT_SLIDERS });
    setCreateHumorStyle("witty");
    setCreateCommStyle("concise");
    setCreateTopicsInput("");
    setCreateError(null);
  };

  const openCreateModal = () => {
    cancelEdit();
    resetCreateForm();
    setShowCreate(true);
  };

  const closeCreateModal = () => {
    setShowCreate(false);
    resetCreateForm();
  };

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!createName.trim()) return;

    setIsCreating(true);
    setCreateError(null);

    try {
      await createCompanion({
        name: createName.trim(),
        persona: createPersona.trim() || null,
        description: null,
        avatar_url: null,
        backstory: createBackstory.trim() || null,
        personality_sliders: createSliders,
        humor_style: createHumorStyle || null,
        communication_style: createCommStyle || null,
        favorite_topics: parseTopics(createTopicsInput),
      });
      setShowCreate(false);
      resetCreateForm();
      await loadCompanions();
    } catch (err) {
      console.error("Failed to create companion", err);
      setCreateError(getErrorDetail(err));
    } finally {
      setIsCreating(false);
    }
  };

  const handleDelete = async (companionId: string) => {
    setIsDeleting(true);
    setDeleteError(null);

    try {
      await deleteCompanion(companionId);
      setCompanions((prev) =>
        prev.filter((c) => c.companion_id !== companionId),
      );
      setConfirmDeleteId(null);
    } catch (err) {
      console.error("Failed to delete companion", err);
      setDeleteError(getErrorDetail(err));
    } finally {
      setIsDeleting(false);
    }
  };

  const startEdit = (companion: Companion) => {
    setShowCreate(false);
    setConfirmDeleteId(null);
    setEditError(null);
    setEditingId(companion.companion_id);
    setEditingCompanion(companion);
    setEditName(companion.name);
    setEditBackstory(companion.backstory ?? "");
    setEditPersona(companion.persona ?? "");
    setEditSliders({
      ...DEFAULT_SLIDERS,
      ...(companion.personality_sliders ?? {}),
    });
    setEditHumorStyle(companion.humor_style ?? "witty");
    setEditCommStyle(companion.communication_style ?? "concise");
    setEditTopicsInput((companion.favorite_topics ?? []).join(", "));
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingCompanion(null);
    setEditName("");
    setEditBackstory("");
    setEditPersona("");
    setEditSliders({ ...DEFAULT_SLIDERS });
    setEditHumorStyle("witty");
    setEditCommStyle("concise");
    setEditTopicsInput("");
    setEditError(null);
  };

  const handleSaveEdit = async (
    event: FormEvent<HTMLFormElement>,
    companionId: string,
  ) => {
    event.preventDefault();

    if (!editName.trim()) return;

    setIsSavingEdit(true);
    setEditError(null);

    try {
      await updateCompanion(companionId, {
        name: editName.trim(),
        persona: editPersona.trim() || null,
        description: editingCompanion?.description ?? null,
        avatar_url: editingCompanion?.avatar_url ?? null,
        backstory: editBackstory.trim() || null,
        personality_sliders: editSliders,
        humor_style: editHumorStyle || null,
        communication_style: editCommStyle || null,
        favorite_topics: parseTopics(editTopicsInput),
      });
      cancelEdit();
      await loadCompanions();
    } catch (err) {
      console.error("Failed to update companion", err);
      setEditError(getErrorDetail(err));
    } finally {
      setIsSavingEdit(false);
    }
  };

  const handleLogout = () => {
    clearAccessToken();
    onLogout();
  };

  return (
    <div className="flex min-h-screen flex-col bg-gray-900 px-4 py-8">
      <div className="mx-auto w-full max-w-3xl">
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-100">
              Your Companions
            </h1>
            <p className="mt-1 text-sm text-gray-400">
              Select a companion or create a new one.
            </p>
          </div>
          <button
            type="button"
            className="rounded-lg border border-gray-600 px-3 py-2 text-sm font-medium text-gray-300 transition hover:border-red-500 hover:text-red-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
            onClick={handleLogout}
          >
            Log out
          </button>
        </div>

        {error ? (
          <div className="mb-4 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
            <button
              type="button"
              className="ml-2 underline hover:no-underline"
              onClick={loadCompanions}
            >
              Retry
            </button>
          </div>
        ) : null}

        {deleteError ? (
          <div className="mb-4 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {deleteError}
            <button
              type="button"
              className="ml-2 underline hover:no-underline"
              onClick={() => setDeleteError(null)}
            >
              Dismiss
            </button>
          </div>
        ) : null}

        {loading ? (
          <div className="text-sm text-gray-400">Loading companions...</div>
        ) : companions.length === 0 ? (
          <div className="text-sm text-gray-500">No companions yet.</div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {companions.map((companion) => (
              <div
                key={companion.companion_id}
                className="flex h-56 flex-col rounded-lg bg-gray-800 p-4 text-gray-100"
              >
                <div className="flex items-center gap-3">
                  <CompanionCardAvatar companion={companion} />
                  <h3 className="min-w-0 truncate text-lg font-semibold">
                    {companion.name}
                  </h3>
                </div>
                <p
                  className="mt-1 text-sm text-gray-400"
                  style={{
                    overflow: "hidden",
                    display: "-webkit-box",
                    WebkitLineClamp: 3,
                    WebkitBoxOrient: "vertical",
                  }}
                >
                  {companion.persona || companion.description || "No description"}
                </p>

                {confirmDeleteId === companion.companion_id ? (
                  <div className="mt-auto flex items-center gap-2 text-sm">
                    <span className="text-red-200">Are you sure?</span>
                    <button
                      type="button"
                      disabled={isDeleting}
                      className="rounded bg-red-700 px-2 py-0.5 text-xs font-medium text-white transition hover:bg-red-600 focus:outline-none focus:ring-2 focus:ring-red-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => handleDelete(companion.companion_id)}
                    >
                      {isDeleting ? "..." : "Yes"}
                    </button>
                    <button
                      type="button"
                      disabled={isDeleting}
                      className="rounded border border-gray-600 px-2 py-0.5 text-xs text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => setConfirmDeleteId(null)}
                    >
                      No
                    </button>
                  </div>
                ) : (
                  <div className="mt-auto flex items-center gap-2">
                    <button
                      type="button"
                      className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800"
                      onClick={() => onSelectCompanion(companion)}
                    >
                      Select
                    </button>
                    <button
                      type="button"
                      className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:border-indigo-500 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                      onClick={() => startEdit(companion)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-400 transition hover:border-red-500 hover:text-red-300 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                      onClick={() =>
                        setConfirmDeleteId(companion.companion_id)
                      }
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        <button
          type="button"
          className="mt-6 flex items-center gap-2 self-start rounded-lg border border-gray-600 px-3 py-2 text-sm font-medium text-gray-300 transition hover:border-indigo-500 hover:bg-gray-700 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
          onClick={openCreateModal}
        >
          <svg
            className="h-4 w-4"
            viewBox="0 0 20 20"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
          >
            <path
              d="M10 4V16M4 10H16"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
          Create New Companion
        </button>

        {showCreate ? (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
            <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100 shadow-2xl">
              <div className="flex items-start justify-between gap-4">
                <h3 className="text-lg font-semibold">
                  Create New Companion
                </h3>
                <button
                  type="button"
                  disabled={isCreating}
                  className="rounded-lg border border-gray-600 px-3 py-1 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={closeCreateModal}
                >
                  Close
                </button>
              </div>

              {createError ? (
                <div className="mt-3 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
                  {createError}
                </div>
              ) : null}

              <form className="mt-4 flex flex-col gap-5" onSubmit={handleCreate}>
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Identity
                </p>

                <label className={LABEL}>
                  Name
                  <input
                    type="text"
                    required
                    value={createName}
                    disabled={isCreating}
                    className={INPUT}
                    placeholder="Companion name"
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setCreateName(e.target.value)
                    }
                  />
                </label>

                <label className={LABEL}>
                  Backstory
                  <textarea
                    rows={3}
                    value={createBackstory}
                    disabled={isCreating}
                    className={`resize-y ${INPUT}`}
                    placeholder="Background, history, and defining context"
                    onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                      setCreateBackstory(e.target.value)
                    }
                  />
                </label>

                <label className={LABEL}>
                  Persona
                  <textarea
                    rows={3}
                    value={createPersona}
                    disabled={isCreating}
                    className={`resize-y ${INPUT}`}
                    placeholder="Describe the companion's personality"
                    onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                      setCreatePersona(e.target.value)
                    }
                  />
                </label>

                <p className="pt-1 text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Personality
                </p>

                <div className="grid gap-4 md:grid-cols-2">
                  <label className={LABEL}>
                    Humor style
                    <select
                      value={createHumorStyle}
                      disabled={isCreating}
                      className={`capitalize ${INPUT}`}
                      onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                        setCreateHumorStyle(e.target.value)
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
                      value={createCommStyle}
                      disabled={isCreating}
                      className={`capitalize ${INPUT}`}
                      onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                        setCreateCommStyle(e.target.value)
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
                      const val = createSliders[dim] ?? DEFAULT_SLIDERS[dim];
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
                            disabled={isCreating}
                            className="h-2 w-full cursor-pointer accent-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
                            onChange={(e: ChangeEvent<HTMLInputElement>) =>
                              setCreateSliders((prev) => ({
                                ...prev,
                                [dim]: Number(e.target.value),
                              }))
                            }
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>

                <label className={LABEL}>
                  Favorite topics
                  <input
                    type="text"
                    value={createTopicsInput}
                    disabled={isCreating}
                    className={INPUT}
                    placeholder="e.g. philosophy, cooking, jazz (comma-separated)"
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setCreateTopicsInput(e.target.value)
                    }
                  />
                </label>

                <div className="flex justify-end gap-3">
                  <button
                    type="button"
                    disabled={isCreating}
                    className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                    onClick={closeCreateModal}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={isCreating || !createName.trim()}
                    className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
                  >
                    {isCreating ? "Creating..." : "Create"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        ) : null}

        {editingId && editingCompanion ? (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
            <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100 shadow-2xl">
              <div className="flex items-start justify-between gap-4">
                <h3 className="text-lg font-semibold">
                  Edit {editingCompanion.name}
                </h3>
                <button
                  type="button"
                  disabled={isSavingEdit}
                  className="rounded-lg border border-gray-600 px-3 py-1 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={cancelEdit}
                >
                  Close
                </button>
              </div>

              {editError ? (
                <div className="mt-3 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
                  {editError}
                </div>
              ) : null}

              <form
                className="mt-4 flex flex-col gap-5"
                onSubmit={(event) => handleSaveEdit(event, editingId)}
              >
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Identity
                </p>

                <label className={LABEL}>
                  Name
                  <input
                    type="text"
                    required
                    value={editName}
                    disabled={isSavingEdit}
                    className={INPUT}
                    placeholder="Companion name"
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setEditName(e.target.value)
                    }
                  />
                </label>

                <label className={LABEL}>
                  Backstory
                  <textarea
                    rows={3}
                    value={editBackstory}
                    disabled={isSavingEdit}
                    className={`resize-y ${INPUT}`}
                    placeholder="Background, history, and defining context"
                    onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                      setEditBackstory(e.target.value)
                    }
                  />
                </label>

                <label className={LABEL}>
                  Persona
                  <textarea
                    rows={3}
                    value={editPersona}
                    disabled={isSavingEdit}
                    className={`resize-y ${INPUT}`}
                    placeholder="Describe the companion's personality"
                    onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                      setEditPersona(e.target.value)
                    }
                  />
                </label>

                <p className="pt-1 text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Personality
                </p>

                <div className="grid gap-4 md:grid-cols-2">
                  <label className={LABEL}>
                    Humor style
                    <select
                      value={editHumorStyle}
                      disabled={isSavingEdit}
                      className={`capitalize ${INPUT}`}
                      onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                        setEditHumorStyle(e.target.value)
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
                      value={editCommStyle}
                      disabled={isSavingEdit}
                      className={`capitalize ${INPUT}`}
                      onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                        setEditCommStyle(e.target.value)
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
                      const val = editSliders[dim] ?? DEFAULT_SLIDERS[dim];
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
                            disabled={isSavingEdit}
                            className="h-2 w-full cursor-pointer accent-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
                            onChange={(e: ChangeEvent<HTMLInputElement>) =>
                              setEditSliders((prev) => ({
                                ...prev,
                                [dim]: Number(e.target.value),
                              }))
                            }
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>

                <label className={LABEL}>
                  Favorite topics
                  <input
                    type="text"
                    value={editTopicsInput}
                    disabled={isSavingEdit}
                    className={INPUT}
                    placeholder="e.g. philosophy, cooking, jazz (comma-separated)"
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      setEditTopicsInput(e.target.value)
                    }
                  />
                </label>

                <div className="flex justify-end gap-3">
                  <button
                    type="button"
                    disabled={isSavingEdit}
                    className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                    onClick={cancelEdit}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={isSavingEdit || !editName.trim()}
                    className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
                  >
                    {isSavingEdit ? "Saving..." : "Save"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default CompanionSelector;
