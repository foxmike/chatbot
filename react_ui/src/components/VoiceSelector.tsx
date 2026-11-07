import { useCallback, useEffect, useState } from "react";
import { getSessionVoice, listVoices, setSessionVoice } from "../api/voices";
import type { SessionVoice, Voice } from "../api/voices";
import { getAvatarSyncBridge } from "../avatar/avatarSync";
import {
  isSelectableValue,
  selectedVoiceValue,
  sessionVoiceOptions,
  shouldRefreshSelection,
  sourceLabel,
  voiceIdForValue,
  voiceName,
  voiceSelectionEvent,
} from "../utils/voices";

interface VoiceSelectorProps {
  /** The conversation whose speaking voice this controls. */
  sessionId: string | null;
  variant?: "chat" | "popup";
}

function errorDetail(error: unknown): string {
  if (typeof error === "object" && error !== null && "detail" in error) {
    return String((error as { detail: unknown }).detail);
  }
  return error instanceof Error ? error.message : "Voice settings could not be loaded";
}

/**
 * "Voice: [name ▼]" — who speaks in this conversation. The chat window and the avatar popup
 * both render this; the backend holds the one selection and both re-read it on change.
 * A change applies from the next speech turn; it never interrupts the current one.
 */
export function VoiceSelector({ sessionId, variant = "chat" }: VoiceSelectorProps) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [selection, setSelection] = useState<SessionVoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    if (!sessionId) {
      setSelection(null);
      return;
    }
    try {
      const [library, current] = await Promise.all([listVoices(), getSessionVoice(sessionId)]);
      setVoices(library.voices);
      setSelection(current);
      setError(current.error);
    } catch (err) {
      setError(errorDetail(err));
    }
  }, [sessionId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The other window changed the selection (or the library): re-read it from the backend.
  useEffect(() => {
    const bridge = getAvatarSyncBridge();
    return bridge.subscribe((event) => {
      if (shouldRefreshSelection(event as { type: string; sessionId?: string }, sessionId)) {
        void refresh();
      }
    });
  }, [refresh, sessionId]);

  useEffect(() => {
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh]);

  const handleChange = async (value: string) => {
    if (!sessionId || !isSelectableValue(voices, value)) return;
    setSaving(true);
    try {
      const updated = await setSessionVoice(sessionId, voiceIdForValue(value));
      setSelection(updated);
      setError(updated.error);
      getAvatarSyncBridge().publish(voiceSelectionEvent(sessionId, voiceIdForValue(value)));
    } catch (err) {
      setError(errorDetail(err));
      void refresh();
    } finally {
      setSaving(false);
    }
  };

  const speaking = voiceName(voices, selection?.effective_voice_id);
  const source = sourceLabel(selection);
  const title = speaking
    ? `Speaking voice: ${speaking}${source ? ` (${source})` : ""}. Changes apply from the next reply.`
    : "Who speaks in this conversation";
  const selectClass =
    variant === "popup"
      ? "rounded-lg border border-gray-700 bg-[#293240] px-2 py-2 text-sm text-white transition hover:bg-[#3b4658] focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
      : "h-8 min-w-36 max-w-56 shrink rounded-md border border-gray-600 bg-gray-900 px-2 text-xs text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50";

  return (
    <label
      className={`flex items-center gap-2 ${variant === "popup" ? "text-sm font-medium text-gray-300" : "shrink-0 text-xs text-gray-300"}`}
      title={title}
    >
      Voice:
      <select
        aria-label="Voice"
        value={selectedVoiceValue(selection)}
        disabled={!sessionId || saving || selection === null}
        className={selectClass}
        onChange={(event) => void handleChange(event.target.value)}
      >
        {sessionVoiceOptions(voices, selection).map((option) => (
          <option key={option.value || "companion-default"} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {error ? (
        <span role="alert" className="max-w-56 truncate text-red-400" title={error}>
          {error}
        </span>
      ) : null}
    </label>
  );
}

export default VoiceSelector;
