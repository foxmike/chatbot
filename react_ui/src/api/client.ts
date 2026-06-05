import type {
  ApiError,
  ChatResponse,
  CharacterProfile,
  CreateSessionResponse,
  IdentityState,
  Message,
  RelationshipConfig,
  Session,
  StreamEvent,
} from "../types";

export interface User {
  user_id: string;
  username: string;
  email: string;
}

export interface AuthTokens {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Companion {
  companion_id: string;
  user_id: string;
  name: string;
  persona: string | null;
  description: string | null;
  avatar_url: string | null;
}

export interface CompanionCreate {
  name: string;
  persona: string | null;
  description: string | null;
  avatar_url: string | null;
}

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

export interface VoicePreferences {
  ttsProviderId: string | null;
  ttsModelId: string | null;
  sttProviderId: string | null;
  sttModelId: string | null;
  voiceSpeed: number | null;
  voicePitch: number | null;
  voiceEnabled: boolean;
}

export interface SynthesizeRequest {
  text: string;
  mood: string | undefined;
  mode: string | undefined;
}

export interface SynthesizeResponse {
  audioUrl: string;
  durationMs: number;
  format: string;
}

export interface TranscribeRequest {
  audioBase64: string;
  format: string;
  language: string | undefined;
}

export interface TranscribeResponse {
  text: string;
  confidence: number;
  languageDetected: string | null;
}

export interface IdentityEvolutionContext {
  topic?: string;
  current_interest?: string;
  tone?: string;
  sentiment_label?: "positive" | "negative";
}

// ---------------------------------------------------------------------------
// Room & Scene types
// ---------------------------------------------------------------------------

export type RoomType =
  | "one_on_one_chat"
  | "roleplay_scene"
  | "project"
  | "emotional_support"
  | "memory_review";

export interface RoomMemoryPolicy {
  persist_to_durable: boolean;
  scope: "room" | "companion" | "global";
  max_context_turns: number;
  include_embeddings: boolean;
}

export interface RoomData {
  room_id: string;
  user_id: string;
  companion_id: string | null;
  room_type: RoomType;
  display_name: string;
  memory_policy: RoomMemoryPolicy;
  scene_id: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface RoomCreateParams {
  user_id: string;
  room_type: RoomType;
  display_name?: string;
  companion_id?: string;
}

export interface MemoryPolicyUpdateParams {
  persist_to_durable?: boolean;
  scope?: string;
  max_context_turns?: number;
  include_embeddings?: boolean;
}

export interface SceneData {
  scene_id: string;
  room_id: string;
  location: string;
  activity: string;
  tone: string;
  intensity: number;
  is_paused: boolean;
  started_at: string;
  paused_at: string | null;
  resumed_at: string | null;
  ended_at: string | null;
  participants: string[];
  scene_context: string;
  metadata: Record<string, unknown>;
}

export interface SceneCreateParams {
  room_id: string;
  location: string;
  activity: string;
}

export interface SceneUpdateParams {
  location?: string;
  activity?: string;
  tone?: string;
  intensity?: number;
  scene_context?: string;
  participants?: string[];
}

export interface PersonaParams {
  name: string;
  description: string;
  traits: string[];
  voice_style: string;
  backstory?: string;
  scene_goals?: string[];
  constraints?: string[];
}

export interface ScenePromptResponse {
  system_prompt: string;
}

export interface ClassificationResponse {
  classification: "scene_only" | "durable" | "both";
}

// ---------------------------------------------------------------------------
// SSE streaming types
// ---------------------------------------------------------------------------

export interface SSEStepEvent {
  step: string;
  status: "running" | "done" | "streaming";
  duration_ms?: number;
}

export interface SSETokenEvent {
  type: "token";
  text: string;
}

export interface SSETypingEvent {
  type: "typing";
  status: "started" | "stopped";
}

export interface SSEResponseEvent {
  type: "response";
  text: string;
}

export interface StreamCallbacks {
  onStep?: (event: SSEStepEvent) => void;
  onToken?: (token: string) => void;
  onTyping?: (status: "started" | "stopped") => void;
  onResponse?: (text: string) => void;
  onError?: (error: Error) => void;
  onDone?: () => void;
}

const BASE_URL = "";
const API_PREFIX = "/api";

let accessToken: string | null = localStorage.getItem("auth_token");

export function setAccessToken(token: string): void {
  accessToken = token;
  localStorage.setItem("auth_token", token);
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function clearAccessToken(): void {
  accessToken = null;
  localStorage.removeItem("auth_token");
}

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

function backendPath(path: string): string {
  if (path.startsWith(`${API_PREFIX}/`) || path === API_PREFIX) {
    return path;
  }

  return `${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string>),
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${BASE_URL}${backendPath(path)}`, {
    ...init,
    headers,
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

export function createSession(title?: string, companionId?: string): Promise<CreateSessionResponse> {
  const body: Record<string, string> = {};
  if (title !== undefined) body.title = title;
  if (companionId !== undefined) body.companion_id = companionId;
  return requestJson<CreateSessionResponse>("/api/sessions/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listSessions(companionId?: string): Promise<Session[]> {
  const url = companionId ? `/api/sessions/?companion_id=${companionId}` : "/api/sessions/";
  return requestJson<
    Array<{
      session_id: string;
      title: string | null;
      created_at: string;
      message_count: number;
    }>
  >(url, {
    method: "GET",
  }).then((sessions) =>
    sessions.map((session, index) => ({
      session_id: session.session_id,
      title: session.title || `Chat ${sessions.length - index}`,
      created_at: session.created_at,
      message_count: session.message_count,
    })),
  );
}

export function getSessionMessages(sessionId: string): Promise<Message[]> {
  return requestJson<
    Array<{
      message_id: string;
      role: string;
      content: string;
      created_at: string;
    }>
  >(`/api/sessions/${sessionId}/messages`, {
    method: "GET",
  }).then((messages) =>
    messages
      .filter((message) => message.role === "user" || message.role === "assistant")
      .map((message) => ({
        message_id: message.message_id,
        role: message.role as Message["role"],
        content: message.content,
        timestamp: message.created_at,
      })),
  );
}

export async function deleteSession(sessionId: string): Promise<void> {
  await requestJson<{ session_id: string; deleted: boolean }>(
    `/api/sessions/${sessionId}`,
    {
      method: "DELETE",
    },
  );
}

export function sendMessage(
  message: string,
  sessionId: string,
  roomId?: string,
): Promise<ChatResponse> {
  return requestJson<ChatResponse>("/api/chat/", {
    method: "POST",
    body: JSON.stringify({ message, session_id: sessionId, room_id: roomId }),
  });
}

export async function streamChat(
  sessionId: string,
  message: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
  roomId?: string,
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${backendPath("/chat/stream")}`, {
      method: "POST",
      headers,
      body: JSON.stringify({ session_id: sessionId, message, room_id: roomId }),
      signal,
    });
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return;
    }
    callbacks.onError?.(
      err instanceof Error ? err : new Error(String(err)),
    );
    return;
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as Partial<ApiError>;
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // swallow parse errors, use statusText
    }
    callbacks.onError?.(new Error(`Stream request failed: ${detail}`));
    return;
  }

  const bodyReader = response.body?.getReader();
  if (!bodyReader) {
    callbacks.onError?.(new Error("Stream response body is not readable"));
    return;
  }
  const reader: ReadableStreamDefaultReader<Uint8Array> = bodyReader;

  const decoder = new TextDecoder();
  let buffer = "";

  async function cleanup(): Promise<void> {
    try {
      await reader.cancel();
    } catch {
      // best-effort
    }
  }

  if (signal) {
    if (signal.aborted) {
      await cleanup();
      return;
    }
    signal.addEventListener("abort", () => {
      cleanup().catch(() => {});
    });
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE events (separated by double newline)
      while (true) {
        const idx = buffer.indexOf("\n\n");
        if (idx === -1) break;

        const rawEvent = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);

        try {
          processSseEvent(rawEvent, callbacks);
        } catch (err: unknown) {
          const error =
            err instanceof Error ? err : new Error(String(err));
          callbacks.onError?.(error);
        }
      }
    }

    // Process any remaining data in buffer
    decoder.decode(); // flush
    if (buffer.trim().length > 0) {
      try {
        processSseEvent(buffer, callbacks);
      } catch (err: unknown) {
        const error =
          err instanceof Error ? err : new Error(String(err));
        callbacks.onError?.(error);
      }
    }
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return;
    }
    callbacks.onError?.(
      err instanceof Error ? err : new Error(String(err)),
    );
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // best-effort
    }
  }
}

// ---------------------------------------------------------------------------
// Internal SSE helpers
// ---------------------------------------------------------------------------

function processSseEvent(
  rawEvent: string,
  callbacks: StreamCallbacks,
): void {
  const dataLines: string[] = [];

  for (const line of rawEvent.split(/\r?\n/)) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }

    const colonIdx = line.indexOf(":");
    if (colonIdx === -1) {
      continue;
    }

    const field = line.slice(0, colonIdx);
    let value = line.slice(colonIdx + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "data") {
      dataLines.push(value);
    }
  }

  const data = dataLines.join("\n");
  if (!data) return;

  // Handle [DONE] sentinel
  if (data === "[DONE]") {
    callbacks.onDone?.();
    return;
  }

  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(data) as Record<string, unknown>;
  } catch {
    // Not valid JSON — ignore non-data or malformed lines
    return;
  }

  // Route by event shape
  if ("step" in parsed && "status" in parsed) {
    const event: SSEStepEvent = {
      step: String(parsed.step),
      status: parsed.status as SSEStepEvent["status"],
      duration_ms:
        typeof parsed.duration_ms === "number"
          ? parsed.duration_ms
          : undefined,
    };
    callbacks.onStep?.(event);
    return;
  }

  if (parsed.type === "token" && typeof parsed.text === "string") {
    callbacks.onToken?.(parsed.text);
    return;
  }

  if (parsed.type === "typing") {
    const status = parsed.status as "started" | "stopped";
    if (status === "started" || status === "stopped") {
      callbacks.onTyping?.(status);
    }
    return;
  }

  if (parsed.type === "response" && typeof parsed.text === "string") {
    callbacks.onResponse?.(parsed.text);
    return;
  }
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
  context?: IdentityEvolutionContext,
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
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }
  const response = await fetch(
    `${BASE_URL}${backendPath(`/api/rituals/${ritualId}`)}`,
    {
      method: "DELETE",
      headers,
    },
  );

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

// ---------------------------------------------------------------------------
// Voice API functions
// ---------------------------------------------------------------------------

export function fetchVoicePreferences(): Promise<VoicePreferences> {
  return requestJson<VoicePreferences>("/api/voice/preferences", {
    method: "GET",
  });
}

export function updateVoicePreferences(
  prefs: Partial<VoicePreferences>,
): Promise<VoicePreferences> {
  return requestJson<VoicePreferences>("/api/voice/preferences", {
    method: "PUT",
    body: JSON.stringify(prefs),
  });
}

export function synthesizeSpeech(
  req: SynthesizeRequest,
): Promise<SynthesizeResponse> {
  return requestJson<SynthesizeResponse>("/api/voice/synthesize", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function transcribeAudio(
  req: TranscribeRequest,
): Promise<TranscribeResponse> {
  return requestJson<TranscribeResponse>("/api/voice/transcribe", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// ---------------------------------------------------------------------------
// Room API functions
// ---------------------------------------------------------------------------

export function createRoom(params: RoomCreateParams): Promise<RoomData> {
  return requestJson<RoomData>("/rooms/", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function listRooms(userId: string): Promise<RoomData[]> {
  return requestJson<{ rooms: RoomData[] }>(`/rooms/${userId}`, {
    method: "GET",
  }).then((res) => res.rooms);
}

export function getRoom(roomId: string): Promise<RoomData> {
  return requestJson<RoomData>(`/rooms/detail/${roomId}`, {
    method: "GET",
  });
}

export function updateRoomMemoryPolicy(
  roomId: string,
  params: MemoryPolicyUpdateParams,
): Promise<RoomData> {
  return requestJson<RoomData>(`/rooms/${roomId}/memory-policy`, {
    method: "PUT",
    body: JSON.stringify(params),
  });
}

export async function deleteRoom(roomId: string): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }
  const response = await fetch(
    `${BASE_URL}${backendPath(`/rooms/${roomId}`)}`,
    {
      method: "DELETE",
      headers,
    },
  );
  await assertOk(response);
}

// ---------------------------------------------------------------------------
// Scene API functions
// ---------------------------------------------------------------------------

export function createScene(
  roomId: string,
  params: SceneCreateParams,
): Promise<SceneData> {
  return requestJson<SceneData>(`/rooms/${roomId}/scenes`, {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function getActiveScene(roomId: string): Promise<SceneData> {
  return requestJson<SceneData>(`/rooms/${roomId}/scenes/active`, {
    method: "GET",
  });
}

export function getScene(sceneId: string): Promise<SceneData> {
  return requestJson<SceneData>(`/rooms/scenes/${sceneId}`, {
    method: "GET",
  });
}

export function updateScene(
  sceneId: string,
  params: SceneUpdateParams,
): Promise<SceneData> {
  return requestJson<SceneData>(`/rooms/scenes/${sceneId}`, {
    method: "PATCH",
    body: JSON.stringify(params),
  });
}

export function pauseScene(sceneId: string): Promise<SceneData> {
  return requestJson<SceneData>(`/rooms/scenes/${sceneId}/pause`, {
    method: "POST",
  });
}

export function resumeScene(sceneId: string): Promise<SceneData> {
  return requestJson<SceneData>(`/rooms/scenes/${sceneId}/resume`, {
    method: "POST",
  });
}

export function endScene(sceneId: string): Promise<SceneData> {
  return requestJson<SceneData>(`/rooms/scenes/${sceneId}/end`, {
    method: "POST",
  });
}

export function setScenePersona(
  sceneId: string,
  params: PersonaParams,
): Promise<ScenePromptResponse> {
  return requestJson<ScenePromptResponse>(`/rooms/scenes/${sceneId}/persona`, {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function classifySceneMemory(
  sceneId: string,
  text: string,
): Promise<ClassificationResponse> {
  return requestJson<ClassificationResponse>(
    `/rooms/scenes/${sceneId}/classify`,
    {
      method: "POST",
      body: JSON.stringify({ text }),
    },
  );
}

export async function signup(
  username: string,
  email: string,
  password: string,
): Promise<AuthTokens> {
  const tokens = await requestJson<AuthTokens>("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ username, email, password }),
  });
  setAccessToken(tokens.access_token);
  return tokens;
}

export async function login(
  email: string,
  password: string,
): Promise<AuthTokens> {
  const tokens = await requestJson<AuthTokens>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setAccessToken(tokens.access_token);
  return tokens;
}

export async function logout(): Promise<void> {
  await requestJson<{ detail: string }>("/auth/logout", {
    method: "POST",
  });
  clearAccessToken();
}

export async function fetchMe(): Promise<User> {
  return requestJson<User>("/auth/me", {
    method: "GET",
  });
}

export async function createCompanion(
  data: CompanionCreate,
): Promise<Companion> {
  return requestJson<Companion>("/companions", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function listCompanions(): Promise<Companion[]> {
  const result = await requestJson<{ companions: Companion[] }>("/companions", {
    method: "GET",
  });
  return result.companions;
}

export async function getCompanion(companionId: string): Promise<Companion> {
  return requestJson<Companion>(`/companions/${companionId}`, {
    method: "GET",
  });
}

export async function updateCompanion(
  companionId: string,
  data: Partial<CompanionCreate>,
): Promise<Companion> {
  return requestJson<Companion>(`/companions/${companionId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteCompanion(companionId: string): Promise<void> {
  const response = await fetch(
    `${BASE_URL}${backendPath(`/companions/${companionId}`)}`,
    {
      method: "DELETE",
      headers: accessToken
        ? {
            "Content-Type": "application/json",
            Authorization: `Bearer ${accessToken}`,
          }
        : { "Content-Type": "application/json" },
    },
  );
  await assertOk(response);
}

// ---------------------------------------------------------------------------
// Admin dashboard types
// ---------------------------------------------------------------------------

export interface TraceSummary {
  traceId: string;
  sessionId: string;
  turnId: string;
  modelId: string | null;
  taskClass: string | null;
  totalLatencyMs: number;
  promptTokens: number;
  completionTokens: number;
  costUsdMicros: number;
  createdAt: string;
}

export interface TraceDetail {
  traceId: string;
  sessionId: string;
  turnId: string;
  modelId: string | null;
  taskClass: string | null;
  totalLatencyMs: number;
  promptTokens: number;
  completionTokens: number;
  costUsdMicros: number;
  createdAt: string;
  userId: string | null;
  modelProvider: string | null;
  prompt: string;
  routingDecision: Record<string, unknown>;
  retrievalSnippets: string[];
  steps: Record<string, unknown>[];
  retrieveLatencyMs: number;
  startedAt: string;
  completedAt: string;
}

export interface TraceListResponse {
  traces: TraceSummary[];
  total: number;
  page: number;
  pageSize: number;
}

export interface MetricsGlobal {
  totalRequests: number;
  totalTokens: number;
  totalCostUsdMicros: number;
  avgLatencyMs: number;
  modelsUsed: number;
}

export interface ModelMetricSummary {
  modelId: string;
  provider: string;
  requestCount: number;
  totalPromptTokens: number;
  totalCompletionTokens: number;
  totalCostUsdMicros: number;
  avgLatencyMs: number;
  p50LatencyMs: number;
  p95LatencyMs: number;
  p99LatencyMs: number;
}

export interface ModelMetricsListResponse {
  models: ModelMetricSummary[];
}

export interface LogEvent {
  eventId: string;
  eventType: string;
  timestamp: string;
  sessionId: string | null;
  turnId: string | null;
  details: Record<string, unknown>;
  severity: string;
}

export interface LogEventsListResponse {
  events: LogEvent[];
  total: number;
}

export interface UsageReport {
  totalSessions: number;
  totalTurns: number;
  totalTokens: number;
  totalCostUsdMicros: number;
  perModel: ModelMetricSummary[];
}

export interface AdminHealth {
  status: string;
  tracesStored: number;
  eventsStored: number;
}

// ---------------------------------------------------------------------------
// Admin dashboard API functions
// ---------------------------------------------------------------------------

export function fetchTraces(
  sessionId?: string,
  page?: number,
  pageSize?: number,
): Promise<TraceListResponse> {
  const params = new URLSearchParams();
  if (sessionId !== undefined) params.set("session_id", sessionId);
  if (page !== undefined) params.set("page", String(page));
  if (pageSize !== undefined) params.set("page_size", String(pageSize));
  const qs = params.toString();
  return requestJson<TraceListResponse>(`/admin/traces${qs ? `?${qs}` : ""}`, {
    method: "GET",
  });
}

export function fetchTraceDetail(traceId: string): Promise<TraceDetail> {
  return requestJson<TraceDetail>(`/admin/traces/${traceId}`, {
    method: "GET",
  });
}

export function fetchMetricsGlobal(): Promise<MetricsGlobal> {
  return requestJson<MetricsGlobal>("/admin/metrics/global", {
    method: "GET",
  });
}

export function fetchModelMetrics(): Promise<ModelMetricsListResponse> {
  return requestJson<ModelMetricsListResponse>("/admin/metrics/models", {
    method: "GET",
  });
}

export function fetchLogEvents(
  eventType?: string,
  sessionId?: string,
  since?: string,
  limit?: number,
  offset?: number,
): Promise<LogEventsListResponse> {
  const params = new URLSearchParams();
  if (eventType !== undefined) params.set("event_type", eventType);
  if (sessionId !== undefined) params.set("session_id", sessionId);
  if (since !== undefined) params.set("since", since);
  if (limit !== undefined) params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  const qs = params.toString();
  return requestJson<LogEventsListResponse>(`/admin/logs${qs ? `?${qs}` : ""}`, {
    method: "GET",
  });
}

export function fetchUsageReport(): Promise<UsageReport> {
  return requestJson<UsageReport>("/admin/usage", {
    method: "GET",
  });
}

export function fetchAdminHealth(): Promise<AdminHealth> {
  return requestJson<AdminHealth>("/admin/health", {
    method: "GET",
  });
}
