import { useCallback, useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import {
  fetchAdminHealth,
  fetchLogEvents,
  fetchMetricsGlobal,
  fetchModelMetrics,
  fetchTraceDetail,
  fetchTraces,
  fetchUsageReport,
} from "../api/client";
import type {
  AdminHealth,
  LogEvent,
  LogEventsListResponse,
  MetricsGlobal,
  ModelMetricsListResponse,
  TraceDetail,
  TraceListResponse,
  UsageReport,
} from "../api/client";
import type { ApiError } from "../types";

type TabKey = "traces" | "metrics" | "logs" | "usage";

const TRACE_PAGE_SIZE = 20;
const LOG_PAGE_SIZE = 20;

function getErrorDetail(error: unknown): string {
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

function formatUsd(costUsdMicros: number): string {
  const dollars = costUsdMicros / 1_000_000;
  if (dollars < 0.01) {
    return `$${dollars.toFixed(6)}`;
  }
  return `$${dollars.toFixed(4)}`;
}

function formatLatency(ms: number): string {
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(2)}s`;
  }
  return `${Math.round(ms)}ms`;
}

function truncateId(id: string, maxLen = 12): string {
  if (id.length <= maxLen) return id;
  return `${id.slice(0, maxLen)}...`;
}

function formatDateTime(value: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value.slice(0, 16);
  }
}

function eventTypeBadgeColor(eventType: string): string {
  switch (eventType) {
    case "memory_write":
      return "border-blue-700 bg-blue-950/50 text-blue-200";
    case "contradiction":
      return "border-red-700 bg-red-950/50 text-red-200";
    case "failed_retrieval":
      return "border-yellow-700 bg-yellow-950/50 text-yellow-200";
    default:
      return "border-gray-600 bg-gray-900 text-gray-300";
  }
}

function severityBadgeColor(severity: string): string {
  switch (severity) {
    case "error":
      return "border-red-700 bg-red-950/50 text-red-200";
    case "warning":
      return "border-yellow-700 bg-yellow-950/50 text-yellow-200";
    case "info":
      return "border-blue-700 bg-blue-950/50 text-blue-200";
    default:
      return "border-gray-600 bg-gray-900 text-gray-300";
  }
}

function StateMessage({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-gray-700 bg-gray-900/70 px-3 py-3 text-sm text-gray-400">
      {message}
    </div>
  );
}

function ErrorMessage({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
      {message}
      {onDismiss ? (
        <button
          type="button"
          className="ml-2 underline hover:no-underline"
          onClick={onDismiss}
        >
          Dismiss
        </button>
      ) : null}
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex items-center gap-2 text-sm text-gray-400 py-4">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-gray-500 border-t-indigo-400" />
      Loading...
    </div>
  );
}

function TabBar({
  activeTab,
  onSelect,
}: {
  activeTab: TabKey;
  onSelect: (tab: TabKey) => void;
}) {
  const tabs: { key: TabKey; label: string; emoji: string }[] = [
    { key: "traces", label: "Traces", emoji: "\uD83D\uDD0D" },
    { key: "metrics", label: "Metrics", emoji: "\uD83D\uDCCA" },
    { key: "logs", label: "Logs", emoji: "\uD83D\uDCCB" },
    { key: "usage", label: "Usage", emoji: "\uD83D\uDCC8" },
  ];

  return (
    <nav className="flex gap-0.5 rounded-lg bg-gray-800 p-1 border border-gray-700" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          role="tab"
          aria-selected={activeTab === tab.key}
          className={`flex-1 rounded-md px-4 py-2 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-indigo-500/60 ${
            activeTab === tab.key
              ? "bg-indigo-600 text-white"
              : "text-gray-400 hover:text-gray-200 hover:bg-gray-700"
          }`}
          onClick={() => onSelect(tab.key)}
        >
          <span className="mr-1.5">{tab.emoji}</span>
          {tab.label}
        </button>
      ))}
    </nav>
  );
}

// ─── Traces Tab ──────────────────────────────────────────────────────────────

function TracesTab() {
  const [traces, setTraces] = useState<TraceListResponse | null>(null);
  const [sessionFilter, setSessionFilter] = useState("");
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null);
  const [expandedDetail, setExpandedDetail] = useState<TraceDetail | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  const loadTraces = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await fetchTraces(
        sessionFilter || undefined,
        page,
        TRACE_PAGE_SIZE,
      );
      setTraces(result);
    } catch (err) {
      console.error("Failed to load traces", err);
      setError(getErrorDetail(err));
    } finally {
      setIsLoading(false);
    }
  }, [sessionFilter, page]);

  useEffect(() => {
    loadTraces();
  }, [loadTraces]);

  const handleToggleExpand = async (traceId: string) => {
    if (expandedTraceId === traceId) {
      setExpandedTraceId(null);
      setExpandedDetail(null);
      return;
    }

    setExpandedTraceId(traceId);
    setExpandedDetail(null);
    setIsLoadingDetail(true);

    try {
      const detail = await fetchTraceDetail(traceId);
      setExpandedDetail(detail);
    } catch (err) {
      console.error("Failed to load trace detail", err);
    } finally {
      setIsLoadingDetail(false);
    }
  };

  const totalPages = traces ? Math.max(1, Math.ceil(traces.total / TRACE_PAGE_SIZE)) : 1;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
        <label className="flex items-center gap-2 text-sm text-gray-300">
          Session ID
          <input
            type="text"
            value={sessionFilter}
            placeholder="Filter by session..."
            className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            onChange={(e: ChangeEvent<HTMLInputElement>) => {
              setSessionFilter(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <button
          type="button"
          className="rounded-lg border border-gray-600 px-3 py-2 text-sm text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
          onClick={() => {
            setPage(1);
            loadTraces();
          }}
        >
          Refresh
        </button>
      </div>

      {error ? <ErrorMessage message={error} onDismiss={() => setError(null)} /> : null}

      {isLoading ? (
        <Spinner />
      ) : !traces || traces.traces.length === 0 ? (
        <StateMessage message="No traces captured yet." />
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-700">
            <table className="min-w-full text-sm text-gray-100">
              <thead className="bg-gray-800 text-left text-xs uppercase text-gray-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Trace ID</th>
                  <th className="px-4 py-3 font-medium">Session ID</th>
                  <th className="px-4 py-3 font-medium">Model</th>
                  <th className="px-4 py-3 font-medium">Task Class</th>
                  <th className="px-4 py-3 font-medium">Latency</th>
                  <th className="px-4 py-3 font-medium">Tokens</th>
                  <th className="px-4 py-3 font-medium">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {traces.traces.map((trace) => {
                  const isExpanded = expandedTraceId === trace.traceId;

                  return (
                    <tr key={trace.traceId} className="group">
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          className="text-indigo-400 transition hover:text-indigo-300 focus:outline-none"
                          onClick={() => handleToggleExpand(trace.traceId)}
                        >
                          {truncateId(trace.traceId)}
                        </button>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-400">
                        {truncateId(trace.sessionId)}
                      </td>
                      <td className="px-4 py-3 text-gray-300">
                        {trace.modelId ?? "\u2014"}
                      </td>
                      <td className="px-4 py-3 text-gray-300">
                        {trace.taskClass ?? "\u2014"}
                      </td>
                      <td className="px-4 py-3 text-gray-300">
                        {formatLatency(trace.totalLatencyMs)}
                      </td>
                      <td className="px-4 py-3 text-gray-300">
                        {trace.promptTokens + trace.completionTokens}
                      </td>
                      <td className="px-4 py-3 text-gray-300">
                        {formatUsd(trace.costUsdMicros)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Expanded detail panels */}
            {expandedTraceId && traces.traces.some((t) => t.traceId === expandedTraceId) ? (
              <div className="border-t border-gray-700 bg-gray-900/70 p-4">
                {isLoadingDetail ? (
                  <div className="text-sm text-gray-400">Loading trace detail...</div>
                ) : expandedDetail ? (
                  <div className="flex flex-col gap-4">
                    {/* Prompt */}
                    <div>
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                        Prompt
                      </h3>
                      <pre className="mt-2 max-h-64 overflow-y-auto rounded-lg border border-gray-700 bg-gray-950 p-3 text-xs text-gray-200 whitespace-pre-wrap break-words">
                        {expandedDetail.prompt}
                      </pre>
                    </div>

                    {/* Routing Decision */}
                    <div>
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                        Routing Decision
                      </h3>
                      <pre className="mt-2 max-h-48 overflow-y-auto rounded-lg border border-gray-700 bg-gray-950 p-3 text-xs text-gray-200 whitespace-pre-wrap break-words">
                        {JSON.stringify(expandedDetail.routingDecision, null, 2)}
                      </pre>
                    </div>

                    {/* Retrieval Snippets */}
                    {expandedDetail.retrievalSnippets.length > 0 ? (
                      <div>
                        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                          Retrieval Snippets ({expandedDetail.retrievalSnippets.length})
                        </h3>
                        <ul className="mt-2 flex flex-col gap-2 max-h-64 overflow-y-auto">
                          {expandedDetail.retrievalSnippets.map((snippet, idx) => (
                            <li
                              key={idx}
                              className="rounded-lg border border-gray-700 bg-gray-950 p-3 text-xs text-gray-300"
                            >
                              {snippet}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {/* Pipeline Steps */}
                    {expandedDetail.steps.length > 0 ? (
                      <div>
                        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                          Pipeline Steps ({expandedDetail.steps.length})
                        </h3>
                        <div className="mt-2 overflow-x-auto rounded-lg border border-gray-700">
                          <table className="min-w-full text-xs text-gray-200">
                            <thead className="bg-gray-800 text-left text-gray-400">
                              <tr>
                                <th className="px-3 py-2 font-medium">Step</th>
                                <th className="px-3 py-2 font-medium">Duration</th>
                                <th className="px-3 py-2 font-medium">Details</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-700">
                              {expandedDetail.steps.map((step, idx) => (
                                <tr key={idx}>
                                  <td className="px-3 py-2 text-gray-300">
                                    {typeof step.step === "string"
                                      ? step.step
                                      : `Step ${idx + 1}`}
                                  </td>
                                  <td className="px-3 py-2 text-gray-400">
                                    {step.duration_ms
                                      ? formatLatency(step.duration_ms as number)
                                      : "\u2014"}
                                  </td>
                                  <td className="px-3 py-2 text-gray-500 max-w-xs truncate">
                                    {step.status ? String(step.status) : "\u2014"}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    ) : null}

                    {/* Cost Breakdown */}
                    <div>
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                        Cost Breakdown
                      </h3>
                      <div className="mt-2 grid gap-2 sm:grid-cols-3">
                        <div className="rounded-lg border border-gray-700 bg-gray-950 p-3 text-sm text-gray-300">
                          <span className="text-gray-500">Total:</span>{" "}
                          {formatUsd(expandedDetail.costUsdMicros)}
                        </div>
                        <div className="rounded-lg border border-gray-700 bg-gray-950 p-3 text-sm text-gray-300">
                          <span className="text-gray-500">Prompt tokens:</span>{" "}
                          {expandedDetail.promptTokens.toLocaleString()}
                        </div>
                        <div className="rounded-lg border border-gray-700 bg-gray-950 p-3 text-sm text-gray-300">
                          <span className="text-gray-500">Completion tokens:</span>{" "}
                          {expandedDetail.completionTokens.toLocaleString()}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <StateMessage message="Failed to load trace detail." />
                )}
              </div>
            ) : null}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400">
              Page {page} of {totalPages} ({traces.total} total)
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={page <= 1}
                className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>
              <button
                type="button"
                disabled={page >= totalPages}
                className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ─── Metrics Tab ─────────────────────────────────────────────────────────────

function LatencyBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <span className="w-8 text-xs text-gray-500 text-right">{label}</span>
      <div className="flex-1 h-4 rounded-full bg-gray-800 overflow-hidden">
        <div
          className="h-full rounded-full bg-indigo-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-gray-300 w-16 text-right">{formatLatency(value)}</span>
    </div>
  );
}

function MetricsTab() {
  const [global, setGlobal] = useState<MetricsGlobal | null>(null);
  const [models, setModels] = useState<ModelMetricsListResponse | null>(null);
  const [isLoadingGlobal, setIsLoadingGlobal] = useState(true);
  const [isLoadingModels, setIsLoadingModels] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function loadGlobal() {
      setIsLoadingGlobal(true);
      try {
        const result = await fetchMetricsGlobal();
        if (isMounted) setGlobal(result);
      } catch (err) {
        console.error("Failed to load global metrics", err);
        if (isMounted) setError(getErrorDetail(err));
      } finally {
        if (isMounted) setIsLoadingGlobal(false);
      }
    }

    async function loadModels() {
      setIsLoadingModels(true);
      try {
        const result = await fetchModelMetrics();
        if (isMounted) setModels(result);
      } catch (err) {
        console.error("Failed to load model metrics", err);
        if (isMounted) setError(getErrorDetail(err));
      } finally {
        if (isMounted) setIsLoadingModels(false);
      }
    }

    loadGlobal();
    loadModels();

    return () => {
      isMounted = false;
    };
  }, []);

  const maxLatency = models
    ? Math.max(...models.models.map((m) => m.p99LatencyMs), 1)
    : 1;

  return (
    <div className="flex flex-col gap-6">
      {error ? <ErrorMessage message={error} onDismiss={() => setError(null)} /> : null}

      {/* Global Summary */}
      {isLoadingGlobal ? (
        <Spinner />
      ) : !global ? (
        <StateMessage message="No metrics available." />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
            <p className="text-xs uppercase tracking-wider text-gray-400">Total Requests</p>
            <p className="mt-1 text-2xl font-semibold text-gray-100">
              {global.totalRequests.toLocaleString()}
            </p>
          </div>
          <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
            <p className="text-xs uppercase tracking-wider text-gray-400">Total Tokens</p>
            <p className="mt-1 text-2xl font-semibold text-gray-100">
              {global.totalTokens.toLocaleString()}
            </p>
          </div>
          <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
            <p className="text-xs uppercase tracking-wider text-gray-400">Total Cost</p>
            <p className="mt-1 text-2xl font-semibold text-gray-100">
              {formatUsd(global.totalCostUsdMicros)}
            </p>
          </div>
          <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
            <p className="text-xs uppercase tracking-wider text-gray-400">Avg Latency</p>
            <p className="mt-1 text-2xl font-semibold text-gray-100">
              {formatLatency(global.avgLatencyMs)}
            </p>
          </div>
        </div>
      )}

      {/* Per-Model Metrics */}
      {isLoadingModels ? (
        <Spinner />
      ) : !models || models.models.length === 0 ? (
        <StateMessage message="No per-model metrics available." />
      ) : (
        <div className="flex flex-col gap-4">
          {/* Table */}
          <div className="overflow-x-auto rounded-lg border border-gray-700">
            <table className="min-w-full text-sm text-gray-100">
              <thead className="bg-gray-800 text-left text-xs uppercase text-gray-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Model</th>
                  <th className="px-4 py-3 font-medium">Requests</th>
                  <th className="px-4 py-3 font-medium">Prompt Tok</th>
                  <th className="px-4 py-3 font-medium">Comp Tok</th>
                  <th className="px-4 py-3 font-medium">Cost</th>
                  <th className="px-4 py-3 font-medium">Avg</th>
                  <th className="px-4 py-3 font-medium">P50</th>
                  <th className="px-4 py-3 font-medium">P95</th>
                  <th className="px-4 py-3 font-medium">P99</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {models.models.map((m) => (
                  <tr key={m.modelId} className="hover:bg-gray-800/50">
                    <td className="px-4 py-3 font-medium text-gray-200">{m.modelId}</td>
                    <td className="px-4 py-3 text-gray-300">{m.requestCount.toLocaleString()}</td>
                    <td className="px-4 py-3 text-gray-300">{m.totalPromptTokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-gray-300">{m.totalCompletionTokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-gray-300">{formatUsd(m.totalCostUsdMicros)}</td>
                    <td className="px-4 py-3 text-gray-300">{formatLatency(m.avgLatencyMs)}</td>
                    <td className="px-4 py-3 text-gray-300">{formatLatency(m.p50LatencyMs)}</td>
                    <td className="px-4 py-3 text-gray-300">{formatLatency(m.p95LatencyMs)}</td>
                    <td className="px-4 py-3 text-gray-300">{formatLatency(m.p99LatencyMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Latency Percentile Bars */}
          <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
            <h3 className="text-sm font-semibold text-gray-100 mb-4">
              Latency Percentiles by Model
            </h3>
            <div className="flex flex-col gap-4">
              {models.models.map((m) => (
                <div key={m.modelId} className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-gray-300">{m.modelId}</span>
                  <LatencyBar value={m.p50LatencyMs} max={maxLatency} label="P50" />
                  <LatencyBar value={m.p95LatencyMs} max={maxLatency} label="P95" />
                  <LatencyBar value={m.p99LatencyMs} max={maxLatency} label="P99" />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Logs Tab ────────────────────────────────────────────────────────────────

function LogsTab() {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [sessionIdFilter, setSessionIdFilter] = useState("");
  const [sinceFilter, setSinceFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);

  const loadEvents = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result: LogEventsListResponse = await fetchLogEvents(
        eventTypeFilter || undefined,
        sessionIdFilter || undefined,
        sinceFilter || undefined,
        LOG_PAGE_SIZE,
        offset,
      );
      setEvents(result.events);
      setTotal(result.total);
    } catch (err) {
      console.error("Failed to load log events", err);
      setError(getErrorDetail(err));
    } finally {
      setIsLoading(false);
    }
  }, [eventTypeFilter, sessionIdFilter, sinceFilter, offset]);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  const totalPages = Math.max(1, Math.ceil(total / LOG_PAGE_SIZE));
  const currentPage = Math.floor(offset / LOG_PAGE_SIZE) + 1;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="flex flex-col gap-1 text-sm text-gray-300">
          Event Type
          <select
            value={eventTypeFilter}
            className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            onChange={(e: ChangeEvent<HTMLSelectElement>) => {
              setEventTypeFilter(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All types</option>
            <option value="memory_write">Memory Write</option>
            <option value="contradiction">Contradiction</option>
            <option value="failed_retrieval">Failed Retrieval</option>
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm text-gray-300">
          Session ID
          <input
            type="text"
            value={sessionIdFilter}
            placeholder="Filter by session..."
            className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            onChange={(e: ChangeEvent<HTMLInputElement>) => {
              setSessionIdFilter(e.target.value);
              setOffset(0);
            }}
          />
        </label>

        <label className="flex flex-col gap-1 text-sm text-gray-300">
          Since
          <input
            type="datetime-local"
            value={sinceFilter}
            className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            onChange={(e: ChangeEvent<HTMLInputElement>) => {
              setSinceFilter(e.target.value);
              setOffset(0);
            }}
          />
        </label>

        <div className="flex items-end">
          <button
            type="button"
            className="rounded-lg border border-gray-600 px-3 py-2 text-sm text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
            onClick={() => {
              setOffset(0);
              loadEvents();
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {error ? <ErrorMessage message={error} onDismiss={() => setError(null)} /> : null}

      {isLoading ? (
        <Spinner />
      ) : events.length === 0 ? (
        <StateMessage message="No events recorded." />
      ) : (
        <>
          <div className="flex flex-col gap-3">
            {events.map((event) => {
              const isExpanded = expandedEventId === event.eventId;

              return (
                <div
                  key={event.eventId}
                  className="rounded-lg border border-gray-700 bg-gray-800 p-4 transition hover:border-gray-600"
                >
                  <div className="flex flex-wrap items-start gap-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${eventTypeBadgeColor(event.eventType)}`}
                    >
                      {event.eventType.replace(/_/g, " ")}
                    </span>
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${severityBadgeColor(event.severity)}`}
                    >
                      {event.severity}
                    </span>
                    <time className="ml-auto text-xs text-gray-500">
                      {formatDateTime(event.timestamp)}
                    </time>
                  </div>

                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
                    {event.sessionId ? (
                      <span>Session: {truncateId(event.sessionId, 16)}</span>
                    ) : null}
                    {event.turnId ? <span>Turn: {truncateId(event.turnId, 16)}</span> : null}
                  </div>

                  <button
                    type="button"
                    className="mt-2 text-xs text-indigo-400 transition hover:text-indigo-300 focus:outline-none"
                    onClick={() =>
                      setExpandedEventId(isExpanded ? null : event.eventId)
                    }
                  >
                    {isExpanded ? "Hide details" : "Show details"}
                  </button>

                  {isExpanded ? (
                    <pre className="mt-3 max-h-64 overflow-y-auto rounded-lg border border-gray-700 bg-gray-950 p-3 text-xs text-gray-200 whitespace-pre-wrap break-words">
                      {JSON.stringify(event.details, null, 2)}
                    </pre>
                  ) : null}
                </div>
              );
            })}
            </div>

          {/* Pagination */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400">
              Page {currentPage} of {totalPages} ({total} total)
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => setOffset((o) => Math.max(0, o - LOG_PAGE_SIZE))}
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + LOG_PAGE_SIZE >= total}
                className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => setOffset((o) => o + LOG_PAGE_SIZE)}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ─── Usage Tab ───────────────────────────────────────────────────────────────

function UsageTab() {
  const [report, setReport] = useState<UsageReport | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function load() {
      setIsLoading(true);
      setError(null);
      try {
        const result = await fetchUsageReport();
        if (isMounted) setReport(result);
      } catch (err) {
        console.error("Failed to load usage report", err);
        if (isMounted) setError(getErrorDetail(err));
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }

    load();

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <div className="flex flex-col gap-6">
      {error ? <ErrorMessage message={error} onDismiss={() => setError(null)} /> : null}

      {isLoading ? (
        <Spinner />
      ) : !report ? (
        <StateMessage message="No usage data available." />
      ) : (
        <>
          {/* Totals Card */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
              <p className="text-xs uppercase tracking-wider text-gray-400">Sessions</p>
              <p className="mt-1 text-2xl font-semibold text-gray-100">
                {report.totalSessions.toLocaleString()}
              </p>
            </div>
            <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
              <p className="text-xs uppercase tracking-wider text-gray-400">Turns</p>
              <p className="mt-1 text-2xl font-semibold text-gray-100">
                {report.totalTurns.toLocaleString()}
              </p>
            </div>
            <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
              <p className="text-xs uppercase tracking-wider text-gray-400">Tokens</p>
              <p className="mt-1 text-2xl font-semibold text-gray-100">
                {report.totalTokens.toLocaleString()}
              </p>
            </div>
            <div className="rounded-lg border border-gray-700 bg-gray-800 p-4">
              <p className="text-xs uppercase tracking-wider text-gray-400">Total Cost</p>
              <p className="mt-1 text-2xl font-semibold text-gray-100">
                {formatUsd(report.totalCostUsdMicros)}
              </p>
            </div>
          </div>

          {/* Per-Model Breakdown */}
          {report.perModel.length > 0 ? (
            <div className="overflow-x-auto rounded-lg border border-gray-700">
              <table className="min-w-full text-sm text-gray-100">
                <thead className="bg-gray-800 text-left text-xs uppercase text-gray-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Model</th>
                    <th className="px-4 py-3 font-medium">Sessions</th>
                    <th className="px-4 py-3 font-medium">Turns</th>
                    <th className="px-4 py-3 font-medium">Tokens</th>
                    <th className="px-4 py-3 font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700">
                  {report.perModel.map((m) => (
                    <tr key={m.modelId} className="hover:bg-gray-800/50">
                      <td className="px-4 py-3 font-medium text-gray-200">{m.modelId}</td>
                      <td className="px-4 py-3 text-gray-300">
                        {/* Sessions not directly in per-model; show request count as proxy */}
                        {m.requestCount.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-gray-300">
                        {/* Turns not directly in per-model; we approximate with request count */}
                        {m.requestCount.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-gray-300">
                        {(m.totalPromptTokens + m.totalCompletionTokens).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-gray-300">
                        {formatUsd(m.totalCostUsdMicros)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <StateMessage message="No per-model usage data available." />
          )}
        </>
      )}
    </div>
  );
}

// ─── AdminDashboard ──────────────────────────────────────────────────────────

export function AdminDashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>("traces");
  const [health, setHealth] = useState<AdminHealth | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [isCheckingHealth, setIsCheckingHealth] = useState(true);

  useEffect(() => {
    let isMounted = true;

    async function checkHealth() {
      setIsCheckingHealth(true);
      try {
        const result = await fetchAdminHealth();
        if (isMounted) {
          setHealth(result);
          setHealthError(false);
        }
      } catch (err) {
        console.error("Admin health check failed", err);
        if (isMounted) {
          setHealthError(true);
          setHealth(null);
        }
      } finally {
        if (isMounted) {
          setIsCheckingHealth(false);
        }
      }
    }

    checkHealth();

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <section className="flex flex-col gap-5 rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Admin Dashboard</h2>
          <p className="mt-1 text-sm text-gray-400">
            Monitor traces, metrics, logs, and usage.
          </p>
        </div>

        {/* Health indicator */}
        <div className="flex items-center gap-2">
          {isCheckingHealth ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-600 bg-gray-900 px-3 py-1 text-xs text-gray-400">
              <span className="h-2 w-2 rounded-full bg-gray-500" />
              Checking...
            </span>
          ) : healthError ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-red-700 bg-red-950/40 px-3 py-1 text-xs text-red-200">
              <span className="h-2 w-2 rounded-full bg-red-400" />
              Admin API Unreachable
            </span>
          ) : health ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-green-700 bg-green-950/40 px-3 py-1 text-xs text-green-200">
              <span className="h-2 w-2 rounded-full bg-green-400" />
              API Healthy
            </span>
          ) : null}

          {health ? (
            <span className="text-xs text-gray-500">
              {health.tracesStored} traces, {health.eventsStored} events stored
            </span>
          ) : null}
        </div>
      </div>

      <TabBar activeTab={activeTab} onSelect={setActiveTab} />

      <div className="min-h-[300px]">
        {activeTab === "traces" ? <TracesTab /> : null}
        {activeTab === "metrics" ? <MetricsTab /> : null}
        {activeTab === "logs" ? <LogsTab /> : null}
        {activeTab === "usage" ? <UsageTab /> : null}
      </div>
    </section>
  );
}

export default AdminDashboard;