import { useEffect, useState } from "react";
import {
  clearAccessToken,
  deleteCompanion,
  listCompanions,
} from "../api/client";
import type { Companion } from "../api/client";
import type { ApiError } from "../types";
import { CompanionEditor } from "./CompanionEditor";
import { CompanionForm } from "./CompanionForm";
import {
  avatarColorForCompanion,
  getInitial,
} from "../utils/avatar";

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
      style={{ backgroundColor: avatarColorForCompanion(companion) }}
    >
      {getInitial(companion.name)}
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

  // ── Delete / modal-edit state ─────────────────────────────────────
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingCompanion, setEditingCompanion] = useState<Companion | null>(
    null,
  );

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

  const openCreateModal = () => {
    cancelEdit();
    setShowCreate(true);
  };

  const closeCreateModal = () => {
    setShowCreate(false);
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
    setEditingId(companion.companion_id);
    setEditingCompanion(companion);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingCompanion(null);
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
                  {companion.avatar_header || companion.persona || "No description"}
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
                  className="rounded-lg border border-gray-600 px-3 py-1 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={closeCreateModal}
                >
                  Close
                </button>
              </div>

              <div className="mt-4">
                <CompanionForm
                  mode="create"
                  showCancel
                  onCancel={closeCreateModal}
                  onSave={(created) => {
                    setCompanions((prev) => [created, ...prev]);
                    setShowCreate(false);
                  }}
                />
              </div>
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
                  title="Close"
                  className="flex h-8 w-8 items-center justify-center rounded-lg border border-gray-700 bg-gray-800 text-gray-400 transition hover:bg-gray-700 hover:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                  onClick={cancelEdit}
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <div className="mt-4">
                <CompanionEditor
                  companion={editingCompanion}
                  showCancel
                  onCancel={cancelEdit}
                  onSave={(updated) => {
                    setCompanions((prev) =>
                      prev.map((c) =>
                        c.companion_id === updated.companion_id ? updated : c,
                      ),
                    );
                    cancelEdit();
                  }}
                />
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default CompanionSelector;
