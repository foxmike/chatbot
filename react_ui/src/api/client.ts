import type {
  ApiError,
  ChatResponse,
  CharacterProfile,
  CreateSessionResponse,
  IdentityState,
  RelationshipConfig,
  StreamEvent,
} from "../types";

export interface MemoryItem {
  id: string;
  content: string;
  score: number;
  created_at: string | null;
  metadata: Record<string, unknown> | null;
  provenance?: Record<string, unknown>;
}

export interface MemoryListResponse {
  memories: MemoryItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface MemorySearchResult {
  domain: string;
  id: string;
  content: string;
  score: number;
  created_at: string | null;
  metadata: Record<string, unknown> | null;
  provenance: Record<string, unknown> | null;
}

export interface MemorySearchResponse {
  results: MemorySearchResult[];
  total: number;
  offset: number;
  limit: number;
}

export interface MemorySearchFilters {
  start?: string;
  end?: string;
  topics?: string[];
  tags?: string[];
  session_id?: string;
  user_id?: string;
  domain?: string;
  offset?: number;
  limit?: number;
}

export interface MemoryExportFilters {
  start?: string;
  end?: string;
  topics?: string[];
  privacy_level?: string;
  session_id?: string;
  user_id?: string;
  include_turns?: boolean;
  include_memories?: boolean;
}

export interface ProvenanceResponse {
  memory_id: string;
  origin: string;
  confidence: number;
  source_message_id: string | null;
  source_turn_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface EditMemoryRequest {
  content?: string;
  metadata?: Record<string, unknown>;
}

export interface MemoryActionResponse {
  [key: string]: unknown;
}

export interface TimelineEntry {
  entry_id: string;
  session_id: string;
  entry_type: "milestone" | "emotional_event" | "promise" | "running_project";
  title: string;
  description: string;
  content: string;
  turn_id: string;
  created_at: string;
  updated_at: string;
  status: "active" | "resolved" | "broken" | "completed" | "abandoned";
  tags: string[];
  metadata: Record<string, unknown>;
}

export interface TimelineFilter {
  session_id: string | null;
  entry_types: string[] | null;
  statuses: string[] | null;
  tags: string[] | null;
  date_from: string | null;
  date_to: string | null;
}

export interface RitualInfo {
  ritual_id: string;
  session_id: string;
  name: string;
  ritual_type: "check_in" | "recap" | "planning" | "recurring" | "joke" | "custom";
  description: string;
  schedule_description: string;
  last_triggered_at: string | null;
  next_trigger_at: string | null;
  is_active: boolean;
  trigger_prompt: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface NarrativeThread {
  thread_id: string;
  session_id: string;
  label: string;
  turns: string[];
  created_at: string;
  last_referenced_at: string;
  relevance_score: number;
  summary: string;
}

export interface ThreadCallback {
  thread_id: string;
  label: string;
  callback_text: string;
  turn_id: string;
  confidence: number;
  thread_summary: string;
}

export interface CreateRitualRequest {
  name: string;
  ritual_type: string;
  description: string;
  schedule_description: string;
  trigger_prompt: string | null;
}

const BASE_URL = "";

async function parseErrorResponse(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ApiError>;

    return {
      detail: typeof body.detail === "string" ? body.detail : response.statusText,
      status_code:
        typeof body.status_code === "number" ? body.status_code : response.status,
    };
  } catch {
    return {
      detail: response.statusText,
      status_code: response.status,
    };
  }
}

async function assertOk(response: Response): Promise<void> {
  if (!response.ok) {
    throw await parseErrorResponse(response);
  }
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });

  await assertOk(response);
  return (await response.json()) as T;
}

function dispatchSseEvent(rawEvent: string, onEvent: (event: StreamEvent) => void) {
  const event: StreamEvent = {
    event: "message",
    data: "",
  };
  const dataLines: string[] = [];
  let hasEventField = false;

  for (const line of rawEvent.split(/\r?\n/)) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }

    const separatorIndex = line.indexOf(":");
    const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex);
    let value = separatorIndex === -1 ? "" : line.slice(separatorIndex + 1);

    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "event") {
      event.event = value;
      hasEventField = true;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  if (!hasEventField && dataLines.length === 0) {
    return;
  }

  event.data = dataLines.join("\n");
  onEvent(event);
}

export function createSession(): Promise<CreateSessionResponse> {
  return requestJson<CreateSessionResponse>("/api/sessions/", {
    method: "POST",
  });
}

export function sendMessage(
  message: string,
  sessionId: string,
): Promise<ChatResponse> {
  return requestJson<ChatResponse>("/api/chat/", {
    method: "POST",
    body: JSON.stringify({ message, session_id: sessionId }),
  });
}

export function getRelationshipConfig(
  sessionId: string,
): Promise<RelationshipConfig> {
  return requestJson<RelationshipConfig>(
    `/api/config/relationship/${sessionId}`,
    {
      method: "GET",
    },
  );
}

export function updateRelationshipConfig(
  sessionId: string,
  config: Partial<RelationshipConfig>,
): Promise<RelationshipConfig> {
  return requestJson<RelationshipConfig>(
    `/api/config/relationship/${sessionId}`,
    {
      method: "PUT",
      body: JSON.stringify(config),
    },
  );
}

export function getCharacterProfile(
  sessionId: string,
): Promise<CharacterProfile> {
  return requestJson<CharacterProfile>(`/api/config/character/${sessionId}`, {
    method: "GET",
  });
}

export function updateCharacterProfile(
  sessionId: string,
  profile: Partial<CharacterProfile>,
): Promise<CharacterProfile> {
  return requestJson<CharacterProfile>(`/api/config/character/${sessionId}`, {
    method: "PUT",
    body: JSON.stringify(profile),
  });
}

export function getIdentityState(sessionId: string): Promise<IdentityState> {
  return requestJson<IdentityState>(`/api/config/identity/${sessionId}`, {
    method: "GET",
  });
}

export function evolveIdentity(
  sessionId: string,
  context?: Record<string, unknown>,
): Promise<IdentityState> {
  return requestJson<IdentityState>(
    `/api/config/identity/${sessionId}/evolve`,
    {
      method: "POST",
      body: JSON.stringify(context === undefined ? {} : { turn_context: context }),
    },
  );
}

export function resetIdentityMood(sessionId: string): Promise<IdentityState> {
  return requestJson<IdentityState>(
    `/api/config/identity/${sessionId}/reset-mood`,
    {
      method: "POST",
    },
  );
}

export function getMemories(
  privacy?: string,
  offset?: number,
  limit?: number,
): Promise<MemoryListResponse> {
  const params = new URLSearchParams();
  if (privacy !== undefined) params.set("privacy", privacy);
  if (offset !== undefined) params.set("offset", String(offset));
  if (limit !== undefined) params.set("limit", String(limit));
  const qs = params.toString();
  return requestJson<MemoryListResponse>(`/api/memories${qs ? `?${qs}` : ""}`, {
    method: "GET",
  });
}

export function toggleMemoryPin(
  memoryId: string,
  pinned: boolean,
): Promise<MemoryActionResponse> {
  return requestJson<MemoryActionResponse>(`/api/memories/${memoryId}/pin`, {
    method: "PATCH",
    body: JSON.stringify({ pinned }),
  });
}

export function deleteMemory(
  memoryId: string,
  hard?: boolean,
): Promise<MemoryActionResponse> {
  const params = new URLSearchParams();
  if (hard !== undefined) params.set("hard", String(hard));
  const qs = params.toString();
  return requestJson<MemoryActionResponse>(
    `/api/memories/${memoryId}${qs ? `?${qs}` : ""}`,
    { method: "DELETE" },
  );
}

export function updateMemory(
  memoryId: string,
  data: EditMemoryRequest,
): Promise<MemoryActionResponse> {
  return requestJson<MemoryActionResponse>(`/api/memories/${memoryId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function setMemoryPrivacy(
  memoryId: string,
  privacyLevel: string,
): Promise<MemoryActionResponse> {
  return requestJson<MemoryActionResponse>(
    `/api/memories/${memoryId}/privacy`,
    {
      method: "PATCH",
      body: JSON.stringify({ level: privacyLevel }),
    },
  );
}

export function doNotRemember(
  memoryId: string,
  sessionId?: string,
): Promise<MemoryActionResponse> {
  const params = new URLSearchParams();
  if (sessionId !== undefined) params.set("session_id", sessionId);
  const qs = params.toString();
  return requestJson<MemoryActionResponse>(
    `/api/memories/${memoryId}/do-not-remember${qs ? `?${qs}` : ""}`,
    { method: "POST" },
  );
}

export function rememberForever(
  memoryId: string,
): Promise<MemoryActionResponse> {
  return requestJson<MemoryActionResponse>(
    `/api/memories/${memoryId}/remember-forever`,
    { method: "POST" },
  );
}

export function correctMemory(
  memoryId: string,
  correction: string,
): Promise<MemoryActionResponse> {
  return requestJson<MemoryActionResponse>(
    `/api/memories/${memoryId}/correct`,
    {
      method: "PATCH",
      body: JSON.stringify({ correction }),
    },
  );
}

export function searchMemories(
  query: string,
  filters?: MemorySearchFilters,
): Promise<MemorySearchResponse> {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  if (filters) {
    if (filters.start) params.set("start", filters.start);
    if (filters.end) params.set("end", filters.end);
    if (filters.topics) {
      for (const t of filters.topics) params.append("topics", t);
    }
    if (filters.tags) {
      for (const t of filters.tags) params.append("tags", t);
    }
    if (filters.session_id) params.set("session_id", filters.session_id);
    if (filters.user_id) params.set("user_id", filters.user_id);
    if (filters.domain) params.set("domain", filters.domain);
    if (filters.offset !== undefined)
      params.set("offset", String(filters.offset));
    if (filters.limit !== undefined)
      params.set("limit", String(filters.limit));
  }
  const qs = params.toString();
  return requestJson<MemorySearchResponse>(
    `/api/memories/search${qs ? `?${qs}` : ""}`,
    { method: "GET" },
  );
}

export function exportMemories(
  filters?: MemoryExportFilters,
  format?: string,
): Promise<unknown> {
  const params = new URLSearchParams();
  if (format) params.set("format", format);
  if (filters) {
    if (filters.start) params.set("start", filters.start);
    if (filters.end) params.set("end", filters.end);
    if (filters.topics) {
      for (const t of filters.topics) params.append("topics", t);
    }
    if (filters.privacy_level)
      params.set("privacy_level", filters.privacy_level);
    if (filters.session_id) params.set("session_id", filters.session_id);
    if (filters.user_id) params.set("user_id", filters.user_id);
    if (filters.include_turns !== undefined)
      params.set("include_turns", String(filters.include_turns));
    if (filters.include_memories !== undefined)
      params.set("include_memories", String(filters.include_memories));
  }
  const qs = params.toString();
  return requestJson<unknown>(
    `/api/memories/export${qs ? `?${qs}` : ""}`,
    { method: "GET" },
  );
}

export function getMemoryProvenance(
  memoryId: string,
): Promise<ProvenanceResponse> {
  return requestJson<ProvenanceResponse>(
    `/api/memories/${memoryId}/provenance`,
    { method: "GET" },
  );
}

export function fetchTimeline(
  sessionId: string,
  filter?: TimelineFilter,
): Promise<TimelineEntry[]> {
  const params = new URLSearchParams();
  if (filter) {
    if (filter.session_id !== null) params.set("session_id", filter.session_id);
    if (filter.entry_types) {
      for (const entryType of filter.entry_types) {
        params.append("entry_types", entryType);
      }
    }
    if (filter.statuses) {
      for (const status of filter.statuses) {
        params.append("statuses", status);
      }
    }
    if (filter.tags) {
      for (const tag of filter.tags) {
        params.append("tags", tag);
      }
    }
    if (filter.date_from !== null) params.set("date_from", filter.date_from);
    if (filter.date_to !== null) params.set("date_to", filter.date_to);
  }
  const qs = params.toString();
  return requestJson<TimelineEntry[]>(
    `/api/timeline/${sessionId}${qs ? `?${qs}` : ""}`,
    { method: "GET" },
  );
}

export function fetchActiveProjects(sessionId: string): Promise<TimelineEntry[]> {
  return requestJson<TimelineEntry[]>(
    `/api/timeline/${sessionId}/projects/active`,
    { method: "GET" },
  );
}

export function fetchPendingPromises(sessionId: string): Promise<TimelineEntry[]> {
  return requestJson<TimelineEntry[]>(
    `/api/timeline/${sessionId}/promises/pending`,
    { method: "GET" },
  );
}

export function resolveTimelineEntry(
  entryId: string,
  resolution?: string,
): Promise<TimelineEntry> {
  return requestJson<TimelineEntry>(`/api/timeline/${entryId}/resolve`, {
    method: "POST",
    body: JSON.stringify(
      resolution === undefined ? {} : { resolution },
    ),
  });
}

export function fetchNarrativeThreads(
  sessionId: string,
): Promise<NarrativeThread[]> {
  return requestJson<NarrativeThread[]>(
    `/api/narrative/${sessionId}/threads`,
    { method: "GET" },
  );
}

export function fetchThreadCallbacks(
  sessionId: string,
  message: string,
): Promise<ThreadCallback[]> {
  return requestJson<ThreadCallback[]>(
    `/api/narrative/${sessionId}/callbacks`,
    {
      method: "POST",
      body: JSON.stringify({ message }),
    },
  );
}

export function fetchRituals(
  sessionId: string,
  type?: string,
): Promise<RitualInfo[]> {
  const params = new URLSearchParams();
  if (type !== undefined) params.set("type", type);
  const qs = params.toString();
  return requestJson<RitualInfo[]>(
    `/api/rituals/${sessionId}${qs ? `?${qs}` : ""}`,
    { method: "GET" },
  );
}

export function createRitual(
  sessionId: string,
  request: CreateRitualRequest,
): Promise<RitualInfo> {
  return requestJson<RitualInfo>(`/api/rituals/${sessionId}`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function deactivateRitual(ritualId: string): Promise<void> {
  const response = await fetch(`${BASE_URL}/api/rituals/${ritualId}`, {
    method: "DELETE",
    headers: {
      "Content-Type": "application/json",
    },
  });

  await assertOk(response);
}

export type InitiativeLevel =
  | "never_proactive"
  | "reminders_only"
  | "gentle_checkins"
  | "frequent"
  | "high_energy";

export interface InitiativeSettings {
  user_id: string;
  level: InitiativeLevel;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
}

export interface InitiativeUpdatePayload {
  level: string;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
}

export interface NotificationItem {
  notification_id: string;
  user_id: string;
  notif_type: string;
  title: string;
  body: string;
  created_at: string;
  read: boolean;
  dismissed: boolean;
  metadata: Record<string, unknown>;
}

export interface NotificationListResponse {
  notifications: NotificationItem[];
  pending_count: number;
}

export interface CheckInTriggerPayload {
  checkin_type: string;
  user_id: string;
  context: Record<string, unknown> | null;
}

export interface CheckInResponse {
  checkin_id: string;
  message: string;
  checkin_type: string;
  priority: number;
}

export interface ScheduleResponse {
  events: Record<string, unknown>[];
  next_event: Record<string, unknown> | null;
}

export function fetchInitiative(userId: string): Promise<InitiativeSettings> {
  return requestJson<InitiativeSettings>(`/agency/initiative/${userId}`, {
    method: "GET",
  });
}

export function updateInitiative(
  userId: string,
  payload: InitiativeUpdatePayload,
): Promise<InitiativeSettings> {
  return requestJson<InitiativeSettings>(`/agency/initiative/${userId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function fetchNotifications(
  userId: string,
  since?: string,
): Promise<NotificationListResponse> {
  const params = new URLSearchParams();
  if (since !== undefined) params.set("since", since);
  const qs = params.toString();
  return requestJson<NotificationListResponse>(
    `/agency/notifications/${userId}${qs ? `?${qs}` : ""}`,
    { method: "GET" },
  );
}

export function markNotificationRead(
  notificationId: string,
): Promise<{ status: string }> {
  return requestJson<{ status: string }>(
    `/agency/notifications/${notificationId}/read`,
    { method: "PUT" },
  );
}

export function dismissNotification(
  notificationId: string,
): Promise<{ status: string }> {
  return requestJson<{ status: string }>(
    `/agency/notifications/${notificationId}/dismiss`,
    { method: "PUT" },
  );
}

export function triggerCheckIn(
  payload: CheckInTriggerPayload,
): Promise<CheckInResponse> {
  return requestJson<CheckInResponse>("/agency/checkins/trigger", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchSchedule(userId: string): Promise<ScheduleResponse> {
  return requestJson<ScheduleResponse>(`/agency/schedule/${userId}`, {
    method: "GET",
  });
}
