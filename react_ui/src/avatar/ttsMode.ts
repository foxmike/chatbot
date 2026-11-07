// How the avatar's spoken reply is cut into text for Qwen3-TTS. It only affects the generated
// speech: microphone input, speech recognition, endpointing and LLM streaming are untouched.
//
//   natural - one TTS request per complete sentence (default; best inflection)
//   fast    - the adaptive low-latency chunking
//
// Add a mode by adding an entry here and to the backend's SPEECH_MODES.

export const TTS_MODES = [
  { id: "natural", label: "Natural" },
  { id: "fast", label: "Fast" },
] as const;

export type TtsMode = (typeof TTS_MODES)[number]["id"];

export const DEFAULT_TTS_MODE: TtsMode = "natural";
export const TTS_MODE_STORAGE_KEY = "avatar_tts_mode";

export function isTtsMode(value: unknown): value is TtsMode {
  return TTS_MODES.some((mode) => mode.id === value);
}

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "setItem">;

/** The saved mode, or the default when nothing valid is saved (or storage is unavailable). */
export function loadTtsMode(storage: ReadableStorage | null): TtsMode {
  try {
    const stored = storage?.getItem(TTS_MODE_STORAGE_KEY);
    return isTtsMode(stored) ? stored : DEFAULT_TTS_MODE;
  } catch {
    return DEFAULT_TTS_MODE;
  }
}

/** Save a mode. Invalid values are rejected (returns false) and nothing is stored. */
export function saveTtsMode(storage: WritableStorage | null, mode: unknown): mode is TtsMode {
  if (!isTtsMode(mode)) return false;
  try {
    storage?.setItem(TTS_MODE_STORAGE_KEY, mode);
  } catch {
    // Persistence is best-effort; the selection still applies to this window.
  }
  return true;
}
