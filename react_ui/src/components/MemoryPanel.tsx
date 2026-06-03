import { useCallback, useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import {
  correctMemory,
  deleteMemory,
  doNotRemember,
  getMemories,
  getMemoryProvenance,
  rememberForever,
  setMemoryPrivacy,
  toggleMemoryPin,
  updateMemory,
} from "../api/client";
import type {
  MemoryItem,
  ProvenanceResponse,
} from "../api/client";
import type { ApiError } from "../types";

interface MemoryPanelProps {
  sessionId?: string;
}

const PAGE_SIZE = 20;

const privacyLevels = ["private", "temporary", "permanent"] as const;

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

function formatDate(value: string | null) {
  if (!value) return "\u2014";
  return value.slice(0, 10);
}

function privacyBadgeColor(level: string) {
  switch (level) {
    case "private":
      return "border-yellow-700 bg-yellow-900/40 text-yellow-200";
    case "temporary":
      return "border-blue-700 bg-blue-900/40 text-blue-200";
    case "permanent":
      return "border-green-700 bg-green-900/40 text-green-200";
    default:
      return "border-gray-600 bg-gray-700 text-gray-300";
  }
}

function extractPrivacyLevel(metadata: Record<string, unknown> | null): string {
  if (metadata && typeof metadata.privacy_level === "string") {
    return metadata.privacy_level;
  }
  return "unknown";
}

function isMemoryPinned(metadata: Record<string, unknown> | null): boolean {
  if (metadata && typeof metadata.pinned === "boolean") {
    return metadata.pinned;
  }
  return false;
}

export function MemoryPanel({ sessionId }: MemoryPanelProps) {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [privacyFilter, setPrivacyFilter] = useState("");
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [provenance, setProvenance] = useState<ProvenanceResponse | null>(null);
  const [isLoadingProvenance, setIsLoadingProvenance] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [correctingId, setCorrectingId] = useState<string | null>(null);
  const [correctionText, setCorrectionText] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const loadMemories = useCallback(
    async (newOffset?: number) => {
      const currentOffset = newOffset ?? offset;
      setIsFetching(true);
      setError(null);

      try {
        const result = await getMemories(
          privacyFilter || undefined,
          currentOffset,
          PAGE_SIZE,
        );
        setMemories(result.memories);
        setTotal(result.total);
      } catch (err) {
        console.error("Failed to load memories", err);
        setError(getErrorDetail(err));
      } finally {
        setIsFetching(false);
      }
    },
    [offset, privacyFilter],
  );

  useEffect(() => {
    loadMemories(0);
    setOffset(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [privacyFilter]);

  const handlePrevPage = () => {
    const newOffset = Math.max(0, offset - PAGE_SIZE);
    setOffset(newOffset);
    loadMemories(newOffset);
  };

  const handleNextPage = () => {
    const newOffset = offset + PAGE_SIZE;
    setOffset(newOffset);
    loadMemories(newOffset);
  };

  const handleToggleExpand = async (memoryId: string) => {
    if (expandedId === memoryId) {
      setExpandedId(null);
      setProvenance(null);
      return;
    }

    setExpandedId(memoryId);
    setProvenance(null);
    setIsLoadingProvenance(true);

    try {
      const prov = await getMemoryProvenance(memoryId);
      setProvenance(prov);
    } catch {
      setProvenance(null);
    } finally {
      setIsLoadingProvenance(false);
    }
  };

  const handlePin = async (memory: MemoryItem) => {
    setActionError(null);
    const pinned = !isMemoryPinned(memory.metadata);
    try {
      await toggleMemoryPin(memory.id, pinned);
      setMemories((prev) =>
        prev.map((m) =>
          m.id === memory.id
            ? { ...m, metadata: { ...m.metadata, pinned } }
            : m,
        ),
      );
    } catch (err) {
      setActionError(getErrorDetail(err));
    }
  };

  const handleDelete = async (memoryId: string) => {
    setActionError(null);
    try {
      await deleteMemory(memoryId);
      setMemories((prev) => prev.filter((m) => m.id !== memoryId));
      setTotal((t) => t - 1);
    } catch (err) {
      setActionError(getErrorDetail(err));
    }
  };

  const handleEditSubmit = async (memoryId: string) => {
    if (!editContent.trim()) return;
    setActionError(null);
    try {
      await updateMemory(memoryId, { content: editContent.trim() });
      setMemories((prev) =>
        prev.map((m) =>
          m.id === memoryId ? { ...m, content: editContent.trim() } : m,
        ),
      );
      setEditingId(null);
      setEditContent("");
    } catch (err) {
      setActionError(getErrorDetail(err));
    }
  };

  const handleStartEdit = (memory: MemoryItem) => {
    setEditingId(memory.id);
    setEditContent(memory.content);
  };

  const handleSetPrivacy = async (memoryId: string, level: string) => {
    setActionError(null);
    try {
      await setMemoryPrivacy(memoryId, level);
      setMemories((prev) =>
        prev.map((m) =>
          m.id === memoryId
            ? { ...m, metadata: { ...m.metadata, privacy_level: level } }
            : m,
        ),
      );
    } catch (err) {
      setActionError(getErrorDetail(err));
    }
  };

  const handleDoNotRemember = async (memoryId: string) => {
    setActionError(null);
    try {
      await doNotRemember(memoryId, sessionId);
      setMemories((prev) => prev.filter((m) => m.id !== memoryId));
      setTotal((t) => t - 1);
    } catch (err) {
      setActionError(getErrorDetail(err));
    }
  };

  const handleRememberForever = async (memoryId: string) => {
    setActionError(null);
    try {
      await rememberForever(memoryId);
      setMemories((prev) =>
        prev.map((m) =>
          m.id === memoryId
            ? { ...m, metadata: { ...m.metadata, privacy_level: "permanent" } }
            : m,
        ),
      );
    } catch (err) {
      setActionError(getErrorDetail(err));
    }
  };

  const handleCorrectSubmit = async (memoryId: string) => {
    if (!correctionText.trim()) return;
    setActionError(null);
    try {
      await correctMemory(memoryId, correctionText.trim());
      setMemories((prev) =>
        prev.map((m) =>
          m.id === memoryId ? { ...m, content: correctionText.trim() } : m,
        ),
      );
      setCorrectingId(null);
      setCorrectionText("");
    } catch (err) {
      setActionError(getErrorDetail(err));
    }
  };

  const handleStartCorrect = (memory: MemoryItem) => {
    setCorrectingId(memory.id);
    setCorrectionText(memory.content);
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-100">Memories</h2>
            <p className="mt-1 text-sm text-gray-400">
              Review and manage companion memories.
            </p>
          </div>

          <label className="flex items-center gap-2 text-sm text-gray-300">
            Privacy
            <select
              value={privacyFilter}
              className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                setPrivacyFilter(e.target.value)
              }
            >
              <option value="">All</option>
              {privacyLevels.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </select>
          </label>
        </div>

        {error ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        {actionError ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {actionError}
            <button
              type="button"
              className="ml-2 underline hover:no-underline"
              onClick={() => setActionError(null)}
            >
              Dismiss
            </button>
          </div>
        ) : null}

        {isFetching ? (
          <div className="text-sm text-gray-400">Loading memories...</div>
        ) : memories.length === 0 ? (
          <div className="text-sm text-gray-500">No memories found.</div>
        ) : (
          <ul className="flex flex-col gap-3 divide-y divide-gray-700">
            {memories.map((memory) => {
              const privacy = extractPrivacyLevel(memory.metadata);
              const pinned = isMemoryPinned(memory.metadata);
              const isExpanded = expandedId === memory.id;
              const isEditing = editingId === memory.id;
              const isCorrecting = correctingId === memory.id;

              return (
                <li key={memory.id} className="pt-3 first:pt-0">
                  <div className="flex flex-col gap-2">
                    <div className="flex flex-wrap items-start gap-2">
                      <span
                        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${privacyBadgeColor(privacy)}`}
                      >
                        {privacy}
                      </span>
                      {pinned ? (
                        <span className="inline-flex items-center rounded-full border border-indigo-700 bg-indigo-900/40 px-2 py-0.5 text-xs font-medium text-indigo-200">
                          pinned
                        </span>
                      ) : null}
                    </div>

                    {isEditing ? (
                      <div className="flex flex-col gap-2">
                        <textarea
                          rows={3}
                          value={editContent}
                          className="resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                          onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                            setEditContent(e.target.value)
                          }
                        />
                        <div className="flex gap-2">
                          <button
                            type="button"
                            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800"
                            onClick={() => handleEditSubmit(memory.id)}
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800"
                            onClick={() => {
                              setEditingId(null);
                              setEditContent("");
                            }}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : isCorrecting ? (
                      <div className="flex flex-col gap-2">
                        <textarea
                          rows={3}
                          value={correctionText}
                          className="resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                          onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                            setCorrectionText(e.target.value)
                          }
                        />
                        <div className="flex gap-2">
                          <button
                            type="button"
                            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800"
                            onClick={() => handleCorrectSubmit(memory.id)}
                          >
                            Save correction
                          </button>
                          <button
                            type="button"
                            className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800"
                            onClick={() => {
                              setCorrectingId(null);
                              setCorrectionText("");
                            }}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <p className="text-sm leading-6 text-gray-100">
                        {memory.content}
                      </p>
                    )}

                    <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                      {memory.created_at ? (
                        <span>{formatDate(memory.created_at)}</span>
                      ) : null}
                      {memory.score !== undefined ? (
                        <span>score: {memory.score.toFixed(3)}</span>
                      ) : null}

                      <button
                        type="button"
                        className="text-xs text-indigo-400 transition hover:text-indigo-300 focus:outline-none"
                        onClick={() => handleToggleExpand(memory.id)}
                      >
                        {isExpanded ? "Hide provenance" : "Provenance"}
                      </button>
                    </div>

                    {isExpanded ? (
                      <div className="rounded-lg border border-gray-700 bg-gray-900 p-3 text-xs text-gray-300">
                        {isLoadingProvenance ? (
                          <span className="text-gray-500">
                            Loading provenance...
                          </span>
                        ) : provenance ? (
                          <div className="flex flex-col gap-1">
                            {provenance.origin ? (
                              <span>origin: {provenance.origin}</span>
                            ) : null}
                            {provenance.source_turn_id ? (
                              <span>
                                source turn: {provenance.source_turn_id}
                              </span>
                            ) : null}
                            {provenance.confidence !== undefined ? (
                              <span>
                                confidence: {provenance.confidence.toFixed(3)}
                              </span>
                            ) : null}
                            {memory.provenance ? (
                              <details className="mt-1">
                                <summary className="cursor-pointer text-gray-400">
                                  Raw provenance
                                </summary>
                                <pre className="mt-1 whitespace-pre-wrap break-all text-gray-500">
                                  {JSON.stringify(
                                    memory.provenance,
                                    null,
                                    2,
                                  )}
                                </pre>
                              </details>
                            ) : provenance ? (
                              <details className="mt-1">
                                <summary className="cursor-pointer text-gray-400">
                                  Raw provenance
                                </summary>
                                <pre className="mt-1 whitespace-pre-wrap break-all text-gray-500">
                                  {JSON.stringify(provenance, null, 2)}
                                </pre>
                              </details>
                            ) : null}
                          </div>
                        ) : (
                          <span className="text-gray-500">
                            No provenance data available.
                          </span>
                        )}
                      </div>
                    ) : null}

                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-400 transition hover:border-gray-500 hover:text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                        title={pinned ? "Unpin" : "Pin"}
                        onClick={() => handlePin(memory)}
                      >
                        {pinned ? "Unpin" : "Pin"}
                      </button>

                      <button
                        type="button"
                        className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-400 transition hover:border-gray-500 hover:text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                        onClick={() => handleStartEdit(memory)}
                      >
                        Edit
                      </button>

                      <button
                        type="button"
                        className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-400 transition hover:border-red-500 hover:text-red-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                        onClick={() => handleDelete(memory.id)}
                      >
                        Delete
                      </button>

                      <button
                        type="button"
                        className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-400 transition hover:border-gray-500 hover:text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                        onClick={() => handleStartCorrect(memory)}
                      >
                        Correct
                      </button>

                      <button
                        type="button"
                        className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-400 transition hover:border-gray-500 hover:text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                        onClick={() => handleDoNotRemember(memory.id)}
                      >
                        Forget
                      </button>

                      <button
                        type="button"
                        className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-400 transition hover:border-green-500 hover:text-green-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                        onClick={() => handleRememberForever(memory.id)}
                      >
                        Forever
                      </button>

                      <div className="relative">
                        <select
                          value=""
                          className="rounded border border-gray-600 bg-gray-800 px-2 py-1 text-xs text-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                          onChange={(e: ChangeEvent<HTMLSelectElement>) => {
                            if (e.target.value) {
                              handleSetPrivacy(memory.id, e.target.value);
                              e.target.value = "";
                            }
                          }}
                        >
                          <option value="">Set privacy</option>
                          {privacyLevels.map((level) => (
                            <option key={level} value={level}>
                              {level}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {total > PAGE_SIZE ? (
          <div className="flex items-center justify-between border-t border-gray-700 pt-3">
            <span className="text-xs text-gray-400">
              {offset + 1}\u2013{Math.min(offset + PAGE_SIZE, total)} of {total}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={handlePrevPage}
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + PAGE_SIZE >= total}
                className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={handleNextPage}
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

export default MemoryPanel;