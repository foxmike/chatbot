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

export function CompanionSelector({
  onSelectCompanion,
  onLogout,
}: CompanionSelectorProps) {
  const [companions, setCompanions] = useState<Companion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createPersona, setCreatePersona] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editPersona, setEditPersona] = useState("");
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
      });
      setShowCreate(false);
      setCreateName("");
      setCreatePersona("");
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
    setConfirmDeleteId(null);
    setEditError(null);
    setEditingId(companion.companion_id);
    setEditName(companion.name);
    setEditPersona(companion.persona ?? "");
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditName("");
    setEditPersona("");
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
        ) : companions.length === 0 && !showCreate ? (
          <div className="text-sm text-gray-500">No companions yet.</div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {companions.map((companion) => (
              <div
                key={companion.companion_id}
                className="flex flex-col rounded-lg bg-gray-800 p-4 text-gray-100"
              >
                {editingId === companion.companion_id ? (
                  <form
                    className="flex flex-1 flex-col gap-4"
                    onSubmit={(event) =>
                      handleSaveEdit(event, companion.companion_id)
                    }
                  >
                    {editError ? (
                      <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
                        {editError}
                      </div>
                    ) : null}

                    <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                      Name
                      <input
                        type="text"
                        required
                        value={editName}
                        disabled={isSavingEdit}
                        className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                        placeholder="Companion name"
                        onChange={(e: ChangeEvent<HTMLInputElement>) =>
                          setEditName(e.target.value)
                        }
                      />
                    </label>

                    <label className="flex flex-1 flex-col gap-2 text-sm font-medium text-gray-200">
                      Persona
                      <textarea
                        rows={3}
                        value={editPersona}
                        disabled={isSavingEdit}
                        className="min-h-[300px] flex-1 resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                        placeholder="Describe the companion's personality"
                        onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                          setEditPersona(e.target.value)
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
                ) : (
                  <>
                    <h3 className="text-lg font-semibold">{companion.name}</h3>
                    <p className="mt-1 text-sm text-gray-400">
                      {companion.persona ||
                        companion.description ||
                        "No description"}
                    </p>
                  </>
                )}

                {editingId !== companion.companion_id &&
                confirmDeleteId === companion.companion_id ? (
                  <div className="mt-3 flex items-center gap-2 text-sm">
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
                ) : editingId !== companion.companion_id ? (
                  <div className="mt-3 flex items-center gap-2">
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
                ) : null}
              </div>
            ))}
          </div>
        )}

        {showCreate ? (
          <div className="mt-6 rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
            <h3 className="text-lg font-semibold">Create New Companion</h3>

            {createError ? (
              <div className="mt-3 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
                {createError}
              </div>
            ) : null}

            <form className="mt-4 flex flex-col gap-4" onSubmit={handleCreate}>
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Name
                <input
                  type="text"
                  required
                  value={createName}
                  disabled={isCreating}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="Companion name"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setCreateName(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Persona
                <textarea
                  rows={3}
                  value={createPersona}
                  disabled={isCreating}
                  className="resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="Describe the companion's personality"
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                    setCreatePersona(e.target.value)
                  }
                />
              </label>

              <div className="flex justify-end gap-3">
                <button
                  type="button"
                  disabled={isCreating}
                  className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => {
                    setShowCreate(false);
                    setCreateError(null);
                  }}
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
        ) : (
          <button
            type="button"
            className="mt-6 flex items-center gap-2 self-start rounded-lg border border-gray-600 px-3 py-2 text-sm font-medium text-gray-300 transition hover:border-indigo-500 hover:bg-gray-700 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
            onClick={() => {
              setCreateError(null);
              setShowCreate(true);
            }}
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
        )}
      </div>
    </div>
  );
}

export default CompanionSelector;
