// Pure Voice Library helpers shared by the chat and avatar-popup voice selectors.
//
// The backend's conversation voice (GET/PUT /voices/sessions/{id}) is the only source of
// truth: both selectors read and write it, and tell each other through the avatar sync
// bridge so the other one re-reads it. Voices are identified by voice_id, never by name.
import type { SessionVoice, Voice, VoiceApiError } from "../api/voices.ts";

/** Select value meaning "no conversation override: use the companion's default voice". */
export const COMPANION_DEFAULT = "";

export const VOICE_SELECTION_EVENT = "VOICE_SELECTION_CHANGED";
export const VOICE_LIBRARY_EVENT = "VOICE_LIBRARY_CHANGED";

export interface VoiceOption {
  value: string;
  label: string;
}

function byLibraryOrder(a: Voice, b: Voice): number {
  if (a.is_builtin !== b.is_builtin) return a.is_builtin ? -1 : 1;
  return a.name.localeCompare(b.name);
}

export function voiceName(voices: Voice[], voiceId: string | null | undefined): string | null {
  if (!voiceId) return null;
  return voices.find((voice) => voice.voice_id === voiceId)?.name ?? null;
}

/** Options for the chat / popup "Voice" dropdown. */
export function sessionVoiceOptions(voices: Voice[], selection: SessionVoice | null): VoiceOption[] {
  const inherited = selection?.companion_voice_id ?? selection?.default_voice_id ?? null;
  const inheritedName = voiceName(voices, inherited);
  return [
    {
      value: COMPANION_DEFAULT,
      label: inheritedName ? `Companion default (${inheritedName})` : "Companion default",
    },
    ...[...voices].sort(byLibraryOrder).map((voice) => ({ value: voice.voice_id, label: voice.name })),
  ];
}

/** Options for a companion's "Default Voice" dropdown. */
export function companionVoiceOptions(voices: Voice[]): VoiceOption[] {
  return [
    { value: COMPANION_DEFAULT, label: "Built-in default" },
    ...[...voices]
      .filter((voice) => !voice.is_builtin)
      .sort(byLibraryOrder)
      .map((voice) => ({ value: voice.voice_id, label: voice.name })),
  ];
}

/** The dropdown value for a conversation: its override, or the companion default. */
export function selectedVoiceValue(selection: SessionVoice | null): string {
  return selection?.override_voice_id ?? COMPANION_DEFAULT;
}

/** The voice_id to send for a dropdown value (null clears the override). */
export function voiceIdForValue(value: string): string | null {
  return value === COMPANION_DEFAULT ? null : value;
}

/** A dropdown value that is not one of the user's voices is refused (never sent). */
export function isSelectableValue(voices: Voice[], value: string): boolean {
  return value === COMPANION_DEFAULT || voices.some((voice) => voice.voice_id === value);
}

export interface VoiceSelectionEvent {
  type: typeof VOICE_SELECTION_EVENT;
  sessionId: string;
  voiceId: string | null;
}

export function voiceSelectionEvent(sessionId: string, voiceId: string | null): VoiceSelectionEvent {
  return { type: VOICE_SELECTION_EVENT, sessionId, voiceId };
}

/** Whether a sync-bridge event means this selector must re-read the conversation's voice. */
export function shouldRefreshSelection(event: { type: string; sessionId?: string }, sessionId: string | null): boolean {
  if (event.type === VOICE_LIBRARY_EVENT) return true;
  return event.type === VOICE_SELECTION_EVENT && !!sessionId && event.sessionId === sessionId;
}

export function methodLabel(method: Voice["method"]): string {
  return method === "designed" ? "Designed" : "Cloned";
}

export function sourceLabel(selection: SessionVoice | null): string | null {
  switch (selection?.effective_source) {
    case "session":
      return "chosen for this conversation";
    case "companion":
      return "companion's voice";
    case "default":
      return "built-in default";
    default:
      return null;
  }
}

/** Human-readable reason a delete was refused because the voice is still in use. */
export function inUseMessage(error: VoiceApiError): string {
  const parts: string[] = [];
  if (error.companions?.length) {
    parts.push(`companions: ${error.companions.map((c) => c.name).join(", ")}`);
  }
  if (error.sessions?.length) {
    parts.push(`${error.sessions.length} conversation${error.sessions.length === 1 ? "" : "s"}`);
  }
  return parts.length ? `${error.detail} (${parts.join("; ")})` : error.detail;
}
