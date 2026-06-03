import { useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  exportMemories,
  searchMemories,
} from "../api/client";
import type {
  MemoryExportFilters,
  MemorySearchFilters,
  MemorySearchResult,
} from "../api/client";
import type { ApiError } from "../types";

interface HistorySearchProps {
  sessionId?: string;
}

const PAGE_SIZE = 20;

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
  if (!value) return "";
  return value.slice(0, 10);
}

function highlightMatches(text: string, query: string) {
  if (!query) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`(${escaped})`, "gi");
  const parts = text.split(regex);
  return parts.map((part, i) =>
    regex.test(part) ? (
      <mark
        key={i}
        className="rounded bg-yellow-500/30 px-0.5 text-yellow-100"
      >
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

export function HistorySearch({ sessionId }: HistorySearchProps) {
  const [query, setQuery] = useState("");
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");
  const [topicInput, setTopicInput] = useState("");
  const [topics, setTopics] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [results, setResults] = useState<MemorySearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [isSearching, setIsSearching] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const buildFilters = (): MemorySearchFilters => {
    const filters: MemorySearchFilters = {};
    if (dateStart) filters.start = new Date(dateStart).toISOString();
    if (dateEnd) filters.end = new Date(dateEnd).toISOString();
    if (topics.length > 0) filters.topics = topics;
    if (tags.length > 0) filters.tags = tags;
    if (sessionId) filters.session_id = sessionId;
    return filters;
  };

  const buildExportFilters = (): MemoryExportFilters => {
    const filters: MemoryExportFilters = {};
    if (dateStart) filters.start = new Date(dateStart).toISOString();
    if (dateEnd) filters.end = new Date(dateEnd).toISOString();
    if (topics.length > 0) filters.topics = topics;
    if (sessionId) filters.session_id = sessionId;
    filters.include_turns = true;
    filters.include_memories = true;
    return filters;
  };

  const handleSearch = async (event?: FormEvent) => {
    if (event) event.preventDefault();
    if (!query.trim()) return;

    setIsSearching(true);
    setError(null);

    try {
      const filters = buildFilters();
      const response = await searchMemories(query.trim(), {
        ...filters,
        offset: 0,
        limit: PAGE_SIZE,
      });
      setResults(response.results);
      setTotal(response.total);
      setOffset(0);
      setHasSearched(true);
    } catch (err) {
      console.error("Search failed", err);
      setError(getErrorDetail(err));
    } finally {
      setIsSearching(false);
    }
  };

  const handlePrevPage = async () => {
    const newOffset = Math.max(0, offset - PAGE_SIZE);
    setIsSearching(true);
    setError(null);

    try {
      const filters = buildFilters();
      const response = await searchMemories(query.trim(), {
        ...filters,
        offset: newOffset,
        limit: PAGE_SIZE,
      });
      setResults(response.results);
      setTotal(response.total);
      setOffset(newOffset);
    } catch (err) {
      setError(getErrorDetail(err));
    } finally {
      setIsSearching(false);
    }
  };

  const handleNextPage = async () => {
    const newOffset = offset + PAGE_SIZE;
    setIsSearching(true);
    setError(null);

    try {
      const filters = buildFilters();
      const response = await searchMemories(query.trim(), {
        ...filters,
        offset: newOffset,
        limit: PAGE_SIZE,
      });
      setResults(response.results);
      setTotal(response.total);
      setOffset(newOffset);
    } catch (err) {
      setError(getErrorDetail(err));
    } finally {
      setIsSearching(false);
    }
  };

  const handleExport = async (format: "json" | "text") => {
    setIsExporting(true);
    setError(null);

    try {
      const exportFilters = buildExportFilters();
      const data = await exportMemories(exportFilters, format);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: format === "json" ? "application/json" : "text/plain",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `memories-export.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(getErrorDetail(err));
    } finally {
      setIsExporting(false);
    }
  };

  const handleAddTopic = () => {
    const trimmed = topicInput.trim();
    if (!trimmed || topics.includes(trimmed)) return;
    setTopics((prev) => [...prev, trimmed]);
    setTopicInput("");
  };

  const handleRemoveTopic = (t: string) => {
    setTopics((prev) => prev.filter((topic) => topic !== t));
  };

  const handleAddTag = () => {
    const trimmed = tagInput.trim();
    if (!trimmed || tags.includes(trimmed)) return;
    setTags((prev) => [...prev, trimmed]);
    setTagInput("");
  };

  const handleRemoveTag = (t: string) => {
    setTags((prev) => prev.filter((tag) => tag !== t));
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <div className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">
            Conversation History
          </h2>
          <p className="mt-1 text-sm text-gray-400">
            Search memories and conversation history.
          </p>
        </div>

        <form className="flex flex-col gap-4" onSubmit={handleSearch}>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              value={query}
              placeholder="Search memories..."
              className="flex-1 rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setQuery(e.target.value)
              }
            />
            <button
              type="submit"
              disabled={isSearching || !query.trim()}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
            >
              {isSearching ? "Searching..." : "Search"}
            </button>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm font-medium text-gray-200">
              Date from
              <input
                type="date"
                value={dateStart}
                className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setDateStart(e.target.value)
                }
              />
            </label>

            <label className="flex flex-col gap-1 text-sm font-medium text-gray-200">
              Date to
              <input
                type="date"
                value={dateEnd}
                className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setDateEnd(e.target.value)
                }
              />
            </label>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label className="text-sm font-medium text-gray-200">
                Topics
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={topicInput}
                  placeholder="Add topic"
                  className="min-w-0 flex-1 rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setTopicInput(e.target.value)
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddTopic();
                    }
                  }}
                />
                <button
                  type="button"
                  disabled={topicInput.trim().length === 0}
                  className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={handleAddTopic}
                >
                  Add
                </button>
              </div>
              {topics.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {topics.map((t) => (
                    <span
                      key={t}
                      className="inline-flex items-center gap-1.5 rounded-full border border-gray-600 bg-gray-700 px-2.5 py-0.5 text-xs text-gray-200"
                    >
                      {t}
                      <button
                        type="button"
                        className="text-gray-400 hover:text-gray-100 focus:outline-none"
                        aria-label={`Remove ${t}`}
                        onClick={() => handleRemoveTopic(t)}
                      >
                        x
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="flex flex-col gap-2">
              <label className="text-sm font-medium text-gray-200">Tags</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={tagInput}
                  placeholder="Add tag"
                  className="min-w-0 flex-1 rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setTagInput(e.target.value)
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddTag();
                    }
                  }}
                />
                <button
                  type="button"
                  disabled={tagInput.trim().length === 0}
                  className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={handleAddTag}
                >
                  Add
                </button>
              </div>
              {tags.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {tags.map((t) => (
                    <span
                      key={t}
                      className="inline-flex items-center gap-1.5 rounded-full border border-gray-600 bg-gray-700 px-2.5 py-0.5 text-xs text-gray-200"
                    >
                      {t}
                      <button
                        type="button"
                        className="text-gray-400 hover:text-gray-100 focus:outline-none"
                        aria-label={`Remove ${t}`}
                        onClick={() => handleRemoveTag(t)}
                      >
                        x
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>

          {error ? (
            <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
              {error}
              <button
                type="button"
                className="ml-2 underline hover:no-underline"
                onClick={() => setError(null)}
              >
                Dismiss
              </button>
            </div>
          ) : null}
        </form>

        {hasSearched ? (
          <div className="flex flex-col gap-3 border-t border-gray-700 pt-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-400">
                {total} result{total !== 1 ? "s" : ""}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={isExporting || total === 0}
                  className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => handleExport("json")}
                >
                  Export JSON
                </button>
                <button
                  type="button"
                  disabled={isExporting || total === 0}
                  className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => handleExport("text")}
                >
                  Export Text
                </button>
              </div>
            </div>

            {isSearching ? (
              <div className="text-sm text-gray-400">Searching...</div>
            ) : results.length === 0 ? (
              <div className="text-sm text-gray-500">No results found.</div>
            ) : (
              <ul className="flex flex-col gap-3 divide-y divide-gray-700">
                {results.map((result) => (
                  <li key={`${result.domain}-${result.id}`} className="pt-3 first:pt-0">
                    <div className="flex flex-col gap-2">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                        <span className="inline-flex items-center rounded-full border border-gray-600 bg-gray-700 px-2 py-0.5 text-xs uppercase text-gray-300">
                          {result.domain}
                        </span>
                        {result.score !== undefined ? (
                          <span>score: {result.score.toFixed(3)}</span>
                        ) : null}
                        {result.created_at ? (
                          <span>{formatDate(result.created_at)}</span>
                        ) : null}
                      </div>
                      <p className="whitespace-pre-wrap text-sm leading-6 text-gray-100">
                        {highlightMatches(result.content, query)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {total > PAGE_SIZE ? (
              <div className="flex items-center justify-between border-t border-gray-700 pt-3">
                <span className="text-xs text-gray-400">
                  {offset + 1}\u2013
                  {Math.min(offset + PAGE_SIZE, total)} of {total}
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
        ) : null}
      </div>
    </section>
  );
}

export default HistorySearch;