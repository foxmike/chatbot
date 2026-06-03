import { useCallback, useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  createRitual,
  deactivateRitual,
  fetchActiveProjects,
  fetchNarrativeThreads,
  fetchPendingPromises,
  fetchRituals,
  fetchTimeline,
  resolveTimelineEntry,
} from "../api/client";
import type {
  CreateRitualRequest,
  NarrativeThread,
  RitualInfo,
  TimelineEntry,
  TimelineFilter,
} from "../api/client";
import type { ApiError } from "../types";

interface TimelinePanelProps {
  sessionId: string;
}

type SectionKey = "timeline" | "projects" | "rituals";

const entryTypes = [
  "milestone",
  "emotional_event",
  "promise",
  "running_project",
] as const;

const statuses = ["active", "resolved", "broken", "completed", "abandoned"] as const;

const ritualTypes = [
  "check_in",
  "recap",
  "planning",
  "recurring",
  "joke",
  "custom",
] as const;

const emptyRitualForm: CreateRitualRequest = {
  name: "",
  ritual_type: "custom",
  description: "",
  schedule_description: "",
  trigger_prompt: null,
};

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

function formatLabel(value: string) {
  return value.replace(/_/g, " ");
}

function formatDateTime(value: string | null) {
  if (!value) return "Never";

  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  } catch {
    return value.slice(0, 16);
  }
}

function entryBadgeColor(entryType: string) {
  switch (entryType) {
    case "milestone":
      return "border-blue-700 bg-blue-950/50 text-blue-200";
    case "emotional_event":
      return "border-pink-700 bg-pink-950/50 text-pink-200";
    case "promise":
      return "border-yellow-700 bg-yellow-950/50 text-yellow-200";
    case "running_project":
      return "border-green-700 bg-green-950/50 text-green-200";
    default:
      return "border-gray-600 bg-gray-900 text-gray-300";
  }
}

function statusBadgeColor(status: string) {
  switch (status) {
    case "active":
      return "border-emerald-700 bg-emerald-950/50 text-emerald-200";
    case "resolved":
    case "completed":
      return "border-indigo-700 bg-indigo-950/50 text-indigo-200";
    case "broken":
    case "abandoned":
      return "border-red-800 bg-red-950/50 text-red-200";
    default:
      return "border-gray-600 bg-gray-900 text-gray-300";
  }
}

function ritualBadgeColor(ritualType: string) {
  switch (ritualType) {
    case "check_in":
      return "border-cyan-700 bg-cyan-950/50 text-cyan-200";
    case "recap":
      return "border-violet-700 bg-violet-950/50 text-violet-200";
    case "planning":
      return "border-amber-700 bg-amber-950/50 text-amber-200";
    case "joke":
      return "border-fuchsia-700 bg-fuchsia-950/50 text-fuchsia-200";
    default:
      return "border-gray-600 bg-gray-900 text-gray-300";
  }
}

function previewText(entry: TimelineEntry) {
  const source = entry.description || entry.content;
  if (source.length <= 140) return source;
  return `${source.slice(0, 137).trimEnd()}...`;
}

function SectionHeader({
  title,
  count,
  isOpen,
  onToggle,
}: {
  title: string;
  count?: number;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-gray-800/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
      onClick={onToggle}
    >
      <span className="flex items-center gap-3">
        <span className="text-sm font-semibold text-gray-100">{title}</span>
        {count !== undefined ? (
          <span className="rounded-full border border-gray-700 bg-gray-900 px-2 py-0.5 text-xs text-gray-300">
            {count}
          </span>
        ) : null}
      </span>
      <span className="text-sm text-gray-400">{isOpen ? "Collapse" : "Expand"}</span>
    </button>
  );
}

function StateMessage({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-gray-700 bg-gray-900/70 px-3 py-3 text-sm text-gray-400">
      {message}
    </div>
  );
}

export function TimelinePanel({ sessionId }: TimelinePanelProps) {
  const [openSections, setOpenSections] = useState<Record<SectionKey, boolean>>({
    timeline: true,
    projects: true,
    rituals: true,
  });
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [activeProjects, setActiveProjects] = useState<TimelineEntry[]>([]);
  const [pendingPromises, setPendingPromises] = useState<TimelineEntry[]>([]);
  const [threads, setThreads] = useState<NarrativeThread[]>([]);
  const [rituals, setRituals] = useState<RitualInfo[]>([]);
  const [entryTypeFilter, setEntryTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [expandedEntryId, setExpandedEntryId] = useState<string | null>(null);
  const [loadingTimeline, setLoadingTimeline] = useState(true);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loadingRituals, setLoadingRituals] = useState(true);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [ritualsError, setRitualsError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isCreatingRitual, setIsCreatingRitual] = useState(false);
  const [ritualForm, setRitualForm] = useState<CreateRitualRequest>(emptyRitualForm);
  const [isSavingRitual, setIsSavingRitual] = useState(false);
  const [resolvingEntryId, setResolvingEntryId] = useState<string | null>(null);
  const [deactivatingRitualId, setDeactivatingRitualId] = useState<string | null>(null);

  const toggleSection = (section: SectionKey) => {
    setOpenSections((current) => ({
      ...current,
      [section]: !current[section],
    }));
  };

  const loadTimeline = useCallback(async () => {
    setLoadingTimeline(true);
    setTimelineError(null);

    const filter: TimelineFilter = {
      session_id: sessionId,
      entry_types: entryTypeFilter ? [entryTypeFilter] : null,
      statuses: statusFilter ? [statusFilter] : null,
      tags: null,
      date_from: null,
      date_to: null,
    };

    try {
      const [entries, narrativeThreads] = await Promise.all([
        fetchTimeline(sessionId, filter),
        fetchNarrativeThreads(sessionId),
      ]);
      setTimeline(entries);
      setThreads(narrativeThreads);
    } catch (error) {
      console.error("Failed to load timeline", error);
      setTimelineError(getErrorDetail(error));
    } finally {
      setLoadingTimeline(false);
    }
  }, [entryTypeFilter, sessionId, statusFilter]);

  const loadProjects = useCallback(async () => {
    setLoadingProjects(true);
    setProjectsError(null);

    try {
      const [projects, promises] = await Promise.all([
        fetchActiveProjects(sessionId),
        fetchPendingPromises(sessionId),
      ]);
      setActiveProjects(projects);
      setPendingPromises(promises);
    } catch (error) {
      console.error("Failed to load projects and promises", error);
      setProjectsError(getErrorDetail(error));
    } finally {
      setLoadingProjects(false);
    }
  }, [sessionId]);

  const loadRituals = useCallback(async () => {
    setLoadingRituals(true);
    setRitualsError(null);

    try {
      const result = await fetchRituals(sessionId);
      setRituals(result);
    } catch (error) {
      console.error("Failed to load rituals", error);
      setRitualsError(getErrorDetail(error));
    } finally {
      setLoadingRituals(false);
    }
  }, [sessionId]);

  useEffect(() => {
    loadTimeline();
  }, [loadTimeline]);

  useEffect(() => {
    loadProjects();
    loadRituals();
  }, [loadProjects, loadRituals]);

  const handleResolveEntry = async (entry: TimelineEntry) => {
    setActionError(null);
    setResolvingEntryId(entry.entry_id);

    try {
      await resolveTimelineEntry(
        entry.entry_id,
        entry.entry_type === "running_project" ? "Completed from timeline panel" : "Resolved from timeline panel",
      );
      await Promise.all([loadTimeline(), loadProjects()]);
    } catch (error) {
      setActionError(getErrorDetail(error));
    } finally {
      setResolvingEntryId(null);
    }
  };

  const handleRitualFormChange =
    (field: keyof CreateRitualRequest) =>
    (event: ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
      const value = event.target.value;
      setRitualForm((current) => ({
        ...current,
        [field]: field === "trigger_prompt" ? value || null : value,
      }));
    };

  const handleCreateRitual = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !ritualForm.name.trim() ||
      !ritualForm.description.trim() ||
      !ritualForm.schedule_description.trim()
    ) {
      return;
    }

    setIsSavingRitual(true);
    setActionError(null);

    try {
      const created = await createRitual(sessionId, {
        ...ritualForm,
        name: ritualForm.name.trim(),
        description: ritualForm.description.trim(),
        schedule_description: ritualForm.schedule_description.trim(),
        trigger_prompt: ritualForm.trigger_prompt?.trim() || null,
      });
      setRituals((current) => [...current, created]);
      setRitualForm(emptyRitualForm);
      setIsCreatingRitual(false);
    } catch (error) {
      setActionError(getErrorDetail(error));
    } finally {
      setIsSavingRitual(false);
    }
  };

  const handleDeactivateRitual = async (ritual: RitualInfo) => {
    setActionError(null);
    setDeactivatingRitualId(ritual.ritual_id);

    try {
      await deactivateRitual(ritual.ritual_id);
      setRituals((current) =>
        current.map((item) =>
          item.ritual_id === ritual.ritual_id
            ? { ...item, is_active: false }
            : item,
        ),
      );
    } catch (error) {
      setActionError(getErrorDetail(error));
    } finally {
      setDeactivatingRitualId(null);
    }
  };

  const projectCards = [...activeProjects, ...pendingPromises];

  return (
    <section className="flex flex-col gap-4 text-gray-100">
      {actionError ? (
        <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
          {actionError}
        </div>
      ) : null}

      <div className="overflow-hidden rounded-lg border border-gray-700 bg-gray-800">
        <SectionHeader
          title="Timeline"
          count={timeline.length}
          isOpen={openSections.timeline}
          onToggle={() => toggleSection("timeline")}
        />
        {openSections.timeline ? (
          <div className="flex flex-col gap-4 border-t border-gray-700 p-4">
            <div className="grid gap-3 md:grid-cols-2">
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Entry type
                <select
                  value={entryTypeFilter}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  onChange={(event) => setEntryTypeFilter(event.target.value)}
                >
                  <option value="">All types</option>
                  {entryTypes.map((type) => (
                    <option key={type} value={type}>
                      {formatLabel(type)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Status
                <select
                  value={statusFilter}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  onChange={(event) => setStatusFilter(event.target.value)}
                >
                  <option value="">All statuses</option>
                  {statuses.map((status) => (
                    <option key={status} value={status}>
                      {formatLabel(status)}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {loadingTimeline ? <StateMessage message="Loading timeline..." /> : null}
            {timelineError ? <StateMessage message={timelineError} /> : null}
            {!loadingTimeline && !timelineError && timeline.length === 0 ? (
              <StateMessage message="No timeline entries yet." />
            ) : null}

            {!loadingTimeline && !timelineError && timeline.length > 0 ? (
              <div className="flex flex-col gap-2">
                {timeline.map((entry) => {
                  const isExpanded = expandedEntryId === entry.entry_id;

                  return (
                    <button
                      key={entry.entry_id}
                      type="button"
                      className="rounded-lg border border-gray-700 bg-gray-900/60 p-3 text-left transition hover:border-gray-600 hover:bg-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                      onClick={() =>
                        setExpandedEntryId(isExpanded ? null : entry.entry_id)
                      }
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs capitalize ${entryBadgeColor(entry.entry_type)}`}
                        >
                          {formatLabel(entry.entry_type)}
                        </span>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs capitalize ${statusBadgeColor(entry.status)}`}
                        >
                          {formatLabel(entry.status)}
                        </span>
                        <time className="ml-auto text-xs text-gray-500" dateTime={entry.created_at}>
                          {formatDateTime(entry.created_at)}
                        </time>
                      </div>
                      <div className="mt-2 text-sm font-semibold text-gray-100">
                        {entry.title}
                      </div>
                      <p className="mt-1 text-sm text-gray-400">
                        {isExpanded ? entry.content || entry.description : previewText(entry)}
                      </p>
                    </button>
                  );
                })}
              </div>
            ) : null}

            <div className="border-t border-gray-700 pt-4">
              <h3 className="text-sm font-semibold text-gray-100">Narrative Threads</h3>
              {loadingTimeline ? (
                <div className="mt-2 text-sm text-gray-500">Loading threads...</div>
              ) : threads.length === 0 ? (
                <div className="mt-2 text-sm text-gray-500">No narrative threads yet.</div>
              ) : (
                <div className="mt-3 grid gap-2">
                  {threads.map((thread) => (
                    <div
                      key={thread.thread_id}
                      className="rounded-lg border border-gray-700 bg-gray-900/50 px-3 py-2"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium text-gray-100">
                          {thread.label}
                        </span>
                        <span className="text-xs text-gray-500">
                          {thread.turns.length} turns
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-gray-400">{thread.summary}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-700 bg-gray-800">
        <SectionHeader
          title="Active Projects & Promises"
          count={projectCards.length}
          isOpen={openSections.projects}
          onToggle={() => toggleSection("projects")}
        />
        {openSections.projects ? (
          <div className="border-t border-gray-700 p-4">
            {loadingProjects ? <StateMessage message="Loading projects and promises..." /> : null}
            {projectsError ? <StateMessage message={projectsError} /> : null}
            {!loadingProjects && !projectsError && projectCards.length === 0 ? (
              <StateMessage message="No active projects or pending promises." />
            ) : null}

            {!loadingProjects && !projectsError && projectCards.length > 0 ? (
              <div className="grid gap-3 md:grid-cols-2">
                {projectCards.map((entry) => (
                  <article
                    key={entry.entry_id}
                    className="rounded-lg border border-gray-700 bg-gray-900/60 p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs capitalize ${entryBadgeColor(entry.entry_type)}`}
                      >
                        {formatLabel(entry.entry_type)}
                      </span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs capitalize ${statusBadgeColor(entry.status)}`}
                      >
                        {formatLabel(entry.status)}
                      </span>
                    </div>
                    <h3 className="mt-3 text-sm font-semibold text-gray-100">
                      {entry.title}
                    </h3>
                    <p className="mt-2 text-sm text-gray-400">{previewText(entry)}</p>
                    <button
                      type="button"
                      disabled={resolvingEntryId === entry.entry_id}
                      className="mt-4 rounded-lg border border-indigo-600 px-3 py-2 text-sm font-medium text-indigo-200 transition hover:bg-indigo-950/50 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-60"
                      onClick={() => handleResolveEntry(entry)}
                    >
                      {entry.entry_type === "running_project" ? "Complete" : "Resolve"}
                    </button>
                  </article>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-700 bg-gray-800">
        <SectionHeader
          title="Rituals"
          count={rituals.length}
          isOpen={openSections.rituals}
          onToggle={() => toggleSection("rituals")}
        />
        {openSections.rituals ? (
          <div className="flex flex-col gap-4 border-t border-gray-700 p-4">
            <div className="flex justify-end">
              <button
                type="button"
                className="rounded-lg border border-gray-600 px-3 py-2 text-sm font-medium text-gray-100 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                onClick={() => setIsCreatingRitual((current) => !current)}
              >
                Create Ritual
              </button>
            </div>

            {isCreatingRitual ? (
              <form
                className="grid gap-3 rounded-lg border border-gray-700 bg-gray-900/60 p-4 md:grid-cols-2"
                onSubmit={handleCreateRitual}
              >
                <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                  Name
                  <input
                    value={ritualForm.name}
                    disabled={isSavingRitual}
                    className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-60"
                    onChange={handleRitualFormChange("name")}
                  />
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                  Type
                  <select
                    value={ritualForm.ritual_type}
                    disabled={isSavingRitual}
                    className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-60"
                    onChange={handleRitualFormChange("ritual_type")}
                  >
                    {ritualTypes.map((type) => (
                      <option key={type} value={type}>
                        {formatLabel(type)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium text-gray-200 md:col-span-2">
                  Description
                  <textarea
                    value={ritualForm.description}
                    disabled={isSavingRitual}
                    rows={2}
                    className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-60"
                    onChange={handleRitualFormChange("description")}
                  />
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                  Schedule
                  <input
                    value={ritualForm.schedule_description}
                    disabled={isSavingRitual}
                    className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-60"
                    onChange={handleRitualFormChange("schedule_description")}
                  />
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                  Trigger prompt
                  <input
                    value={ritualForm.trigger_prompt || ""}
                    disabled={isSavingRitual}
                    className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:opacity-60"
                    onChange={handleRitualFormChange("trigger_prompt")}
                  />
                </label>
                <div className="flex gap-2 md:col-span-2">
                  <button
                    type="submit"
                    disabled={isSavingRitual}
                    className="rounded-lg border border-indigo-600 px-3 py-2 text-sm font-medium text-indigo-200 transition hover:bg-indigo-950/50 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    disabled={isSavingRitual}
                    className="rounded-lg border border-gray-600 px-3 py-2 text-sm font-medium text-gray-200 transition hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-60"
                    onClick={() => setIsCreatingRitual(false)}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : null}

            {loadingRituals ? <StateMessage message="Loading rituals..." /> : null}
            {ritualsError ? <StateMessage message={ritualsError} /> : null}
            {!loadingRituals && !ritualsError && rituals.length === 0 ? (
              <StateMessage message="No rituals configured." />
            ) : null}

            {!loadingRituals && !ritualsError && rituals.length > 0 ? (
              <div className="grid gap-3">
                {rituals.map((ritual) => (
                  <article
                    key={ritual.ritual_id}
                    className="rounded-lg border border-gray-700 bg-gray-900/60 p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-gray-100">
                        {ritual.name}
                      </span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs capitalize ${ritualBadgeColor(ritual.ritual_type)}`}
                      >
                        {formatLabel(ritual.ritual_type)}
                      </span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs ${
                          ritual.is_active
                            ? "border-emerald-700 bg-emerald-950/50 text-emerald-200"
                            : "border-gray-600 bg-gray-900 text-gray-400"
                        }`}
                      >
                        {ritual.is_active ? "Active" : "Inactive"}
                      </span>
                    </div>
                    <p className="mt-2 text-sm text-gray-400">{ritual.description}</p>
                    <div className="mt-3 grid gap-1 text-xs text-gray-500 md:grid-cols-2">
                      <div>Schedule: {ritual.schedule_description}</div>
                      <div>Last triggered: {formatDateTime(ritual.last_triggered_at)}</div>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={ritual.is_active}
                      disabled={!ritual.is_active || deactivatingRitualId === ritual.ritual_id}
                      className="mt-4 flex items-center gap-2 rounded-lg border border-gray-600 px-3 py-2 text-sm font-medium text-gray-200 transition hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-60"
                      onClick={() => handleDeactivateRitual(ritual)}
                    >
                      <span
                        className={`flex h-5 w-9 items-center rounded-full px-0.5 transition ${
                          ritual.is_active ? "bg-emerald-600" : "bg-gray-700"
                        }`}
                      >
                        <span
                          className={`h-4 w-4 rounded-full bg-white transition ${
                            ritual.is_active ? "translate-x-4" : "translate-x-0"
                          }`}
                        />
                      </span>
                      {ritual.is_active ? "Active" : "Inactive"}
                    </button>
                  </article>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}

export default TimelinePanel;
