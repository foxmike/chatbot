// Voice Library API. Voices are identified by voice_id only, never by name.
import { authorizedFetch, authorizedRequest } from "./client.ts";

export type VoiceMethod = "designed" | "cloned";

export interface Voice {
  voice_id: string;
  name: string;
  method: VoiceMethod;
  description: string | null;
  reference_transcript: string;
  language: string;
  model_family: string;
  creation_model: string;
  synthesis_model: string;
  reference_duration: number;
  reference_sample_rate: number;
  reference_sha256: string;
  status: string;
  is_builtin: boolean;
  created_at: string | null;
  updated_at: string | null;
  metadata: Record<string, unknown>;
}

export interface VoiceList {
  voices: Voice[];
  default_voice_id: string;
  default_initialized: boolean;
  languages: string[];
}

export interface VoicePreview {
  preview_id: string;
  kind: "design" | "clone";
  description: string | null;
  transcript: string | null;
  preview_text: string | null;
  language: string;
  reference_duration: number;
  has_sample: boolean;
  suggested_transcript: string | null;
  consent: boolean | null;
  warnings: string[] | null;
}

export interface VoiceReferences {
  companions: { companion_id: string; name: string }[];
  sessions: { session_id: string; title: string | null }[];
}

export interface SessionVoice {
  session_id: string;
  override_voice_id: string | null;
  companion_id: string | null;
  companion_voice_id: string | null;
  default_voice_id: string | null;
  effective_voice_id: string | null;
  effective_source: "session" | "companion" | "default" | null;
  error: string | null;
}

/** An API error that may carry details (e.g. which companions still use a voice). */
export interface VoiceApiError {
  detail: string;
  status_code: number;
  companions?: VoiceReferences["companions"];
  sessions?: VoiceReferences["sessions"];
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const sendsJson = typeof init.body === "string";
  const response = await authorizedFetch(path, {
    ...init,
    headers: sendsJson
      ? { "Content-Type": "application/json", ...(init.headers as Record<string, string>) }
      : init.headers,
  });
  return (await response.json()) as T;
}

async function blob(path: string, init: RequestInit = {}): Promise<Blob> {
  const response = await authorizedFetch(path, init);
  return response.blob();
}

export function listVoices(): Promise<VoiceList> {
  return json<VoiceList>("/voices", { method: "GET" });
}

export function getVoice(voiceId: string): Promise<Voice & { references: VoiceReferences }> {
  return json(`/voices/${encodeURIComponent(voiceId)}`, { method: "GET" });
}

export function createDesignPreview(body: {
  description: string;
  preview_text: string;
  language: string;
}): Promise<VoicePreview> {
  return json<VoicePreview>("/voices/previews/design", { method: "POST", body: JSON.stringify(body) });
}

export function uploadVoiceReference(file: File | Blob, options: {
  filename: string;
  consent: boolean;
  language: string;
  suggestTranscript?: boolean;
}): Promise<VoicePreview> {
  const form = new FormData();
  form.append("file", file, options.filename);
  form.append("consent", options.consent ? "true" : "false");
  form.append("language", options.language);
  form.append("suggest_transcript", options.suggestTranscript === false ? "false" : "true");
  return json<VoicePreview>("/voices/references", { method: "POST", body: form });
}

export function createCloneSample(previewId: string, body: {
  transcript: string;
  preview_text: string;
  language: string;
}): Promise<VoicePreview> {
  return json<VoicePreview>(`/voices/previews/${encodeURIComponent(previewId)}/sample`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchPreviewAudio(previewId: string, which: "reference" | "sample"): Promise<Blob> {
  return blob(`/voices/previews/${encodeURIComponent(previewId)}/audio/${which}`, { method: "GET" });
}

export async function cancelPreview(previewId: string): Promise<void> {
  await authorizedFetch(`/voices/previews/${encodeURIComponent(previewId)}`, { method: "DELETE" });
}

export function saveVoice(body: {
  preview_id: string;
  name: string;
  description?: string | null;
  language?: string;
  transcript?: string | null;
  consent?: boolean;
}): Promise<Voice> {
  return json<Voice>("/voices", { method: "POST", body: JSON.stringify(body) });
}

export function updateVoice(voiceId: string, body: {
  name?: string;
  description?: string | null;
  language?: string;
  transcript?: string;
}): Promise<Voice> {
  return json<Voice>(`/voices/${encodeURIComponent(voiceId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** Delete a voice. A voice still in use is refused (409) unless a replacement is given. */
export async function deleteVoice(voiceId: string, options: {
  replacementVoiceId?: string;
  clearSessionOverrides?: boolean;
} = {}): Promise<void> {
  const params = new URLSearchParams();
  if (options.replacementVoiceId) params.set("replacement_voice_id", options.replacementVoiceId);
  if (options.clearSessionOverrides) params.set("clear_session_overrides", "true");
  const query = params.toString();
  const response = await authorizedRequest(
    `/voices/${encodeURIComponent(voiceId)}${query ? `?${query}` : ""}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    let body: Partial<VoiceApiError> = {};
    try {
      body = (await response.json()) as Partial<VoiceApiError>;
    } catch {
      // keep the status text below
    }
    const error: VoiceApiError = {
      detail: typeof body.detail === "string" ? body.detail : response.statusText,
      status_code: response.status,
      companions: body.companions,
      sessions: body.sessions,
    };
    throw error;
  }
}

export function fetchVoiceAudio(voiceId: string): Promise<Blob> {
  return blob(`/voices/${encodeURIComponent(voiceId)}/audio`, { method: "GET" });
}

export function speakWithVoice(voiceId: string, text: string): Promise<Blob> {
  return blob(`/voices/${encodeURIComponent(voiceId)}/speak`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

export function getSessionVoice(sessionId: string): Promise<SessionVoice> {
  return json<SessionVoice>(`/voices/sessions/${encodeURIComponent(sessionId)}`, { method: "GET" });
}

/** Choose who speaks in a conversation; null returns to the companion's default voice. */
export function setSessionVoice(sessionId: string, voiceId: string | null): Promise<SessionVoice> {
  return json<SessionVoice>(`/voices/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PUT",
    body: JSON.stringify({ voice_id: voiceId }),
  });
}

/** Admin: render the built-in default voice's reference (explicit initialization). */
export function initializeDefaultVoice(replace = false): Promise<{ voice_id: string; replaced: boolean }> {
  return json(`/voices/default/initialize${replace ? "?replace=true" : ""}`, { method: "POST" });
}
