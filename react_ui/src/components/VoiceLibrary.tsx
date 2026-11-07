import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import {
  cancelPreview,
  createCloneSample,
  createDesignPreview,
  deleteVoice,
  fetchPreviewAudio,
  fetchVoiceAudio,
  initializeDefaultVoice,
  listVoices,
  saveVoice,
  setSessionVoice,
  speakWithVoice,
  updateVoice,
  uploadVoiceReference,
} from "../api/voices";
import type { Voice, VoiceApiError, VoicePreview } from "../api/voices";
import { getAvatarSyncBridge } from "../avatar/avatarSync";
import { VOICE_LIBRARY_EVENT, inUseMessage, methodLabel, voiceSelectionEvent } from "../utils/voices";

const INPUT =
  "w-full rounded-md border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-60";
const LABEL = "flex flex-col gap-1 text-sm font-medium text-gray-200";
const BUTTON =
  "rounded-md border border-gray-600 bg-gray-800 px-3 py-1.5 text-sm text-gray-100 transition hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY =
  "rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50";
const DANGER =
  "rounded-md border border-red-700/60 bg-red-950/40 px-3 py-1.5 text-sm text-red-200 transition hover:bg-red-900/50 disabled:opacity-50";

const EXAMPLE_DESCRIPTION =
  "A warm, natural young adult American woman with a relaxed, expressive conversational voice.";
const EXAMPLE_TEXT =
  "Hi, it's really good to hear from you. I was just thinking about how quiet the evening has been.";

function detail(error: unknown): string {
  if (typeof error === "object" && error !== null && "detail" in error) {
    return String((error as { detail: unknown }).detail);
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

function announceLibraryChange() {
  getAvatarSyncBridge().publish({ type: VOICE_LIBRARY_EVENT });
}

/** Plays authenticated audio (fetched as a blob; private audio is never a public URL). */
function useAudioUrl() {
  const urls = useRef<string[]>([]);
  useEffect(() => () => urls.current.forEach((url) => URL.revokeObjectURL(url)), []);
  return (blob: Blob) => {
    const url = URL.createObjectURL(blob);
    urls.current.push(url);
    return url;
  };
}

interface Candidate {
  preview: VoicePreview;
  audioUrl: string;
  name: string;
  saving: boolean;
}

// ------------------------------------------------------------------ Design Voice

function DesignVoice({ languages, onSaved }: { languages: string[]; onSaved: (voice: Voice) => void }) {
  const [description, setDescription] = useState(EXAMPLE_DESCRIPTION);
  const [previewText, setPreviewText] = useState(EXAMPLE_TEXT);
  const [language, setLanguage] = useState("English");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toUrl = useAudioUrl();
  const pending = useRef<Candidate[]>([]);
  pending.current = candidates;

  // Unsaved candidates are discarded when the designer closes.
  useEffect(() => () => {
    pending.current.forEach((candidate) => void cancelPreview(candidate.preview.preview_id).catch(() => {}));
  }, []);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      const preview = await createDesignPreview({ description, preview_text: previewText, language });
      const audioUrl = toUrl(await fetchPreviewAudio(preview.preview_id, "reference"));
      setCandidates((current) => [{ preview, audioUrl, name: "", saving: false }, ...current]);
    } catch (err) {
      setError(detail(err));
    } finally {
      setBusy(false);
    }
  };

  const discard = async (candidate: Candidate) => {
    setCandidates((current) => current.filter((c) => c !== candidate));
    await cancelPreview(candidate.preview.preview_id).catch(() => {});
  };

  const save = async (candidate: Candidate) => {
    setCandidates((current) => current.map((c) => (c === candidate ? { ...c, saving: true } : c)));
    setError(null);
    try {
      const voice = await saveVoice({ preview_id: candidate.preview.preview_id, name: candidate.name });
      setCandidates((current) => current.filter((c) => c.preview.preview_id !== candidate.preview.preview_id));
      onSaved(voice);
    } catch (err) {
      setError(detail(err));
      setCandidates((current) => current.map((c) => (c.preview.preview_id === candidate.preview.preview_id
        ? { ...c, saving: false } : c)));
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <label className={LABEL}>
        Voice Description
        <textarea rows={3} className={`resize-y ${INPUT}`} value={description} disabled={busy}
          onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setDescription(e.target.value)} />
      </label>
      <label className={LABEL}>
        Preview Text
        <textarea rows={2} className={`resize-y ${INPUT}`} value={previewText} disabled={busy}
          onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPreviewText(e.target.value)} />
        <span className="text-xs font-normal text-gray-500">
          The approved recording of this text becomes the voice's permanent reference (aim for 5–15 s).
        </span>
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <LanguageSelect languages={languages} value={language} onChange={setLanguage} disabled={busy} />
        <button type="button" className={PRIMARY} disabled={busy || !description.trim() || !previewText.trim()}
          onClick={() => void generate()}>
          {busy ? "Generating…" : candidates.length ? "Generate Another" : "Generate Preview"}
        </button>
        {busy ? <span className="text-xs text-gray-400">The first preview loads VoiceDesign; this can take a while.</span> : null}
      </div>
      {error ? <p role="alert" className="text-sm text-red-400">{error}</p> : null}
      {candidates.map((candidate, index) => (
        <div key={candidate.preview.preview_id} className="rounded-lg border border-gray-700 bg-gray-900/60 p-3">
          <div className="mb-2 flex items-center justify-between gap-2 text-xs text-gray-400">
            <span>Candidate {candidates.length - index} · {candidate.preview.reference_duration.toFixed(1)}s</span>
            <button type="button" className={BUTTON} onClick={() => void discard(candidate)}>Discard</button>
          </div>
          <audio controls src={candidate.audioUrl} className="w-full" />
          {candidate.preview.warnings?.map((warning) => (
            <p key={warning} className="mt-1 text-xs text-amber-300">{warning}</p>
          ))}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input className={`${INPUT} max-w-xs`} placeholder="Voice name" value={candidate.name}
              onChange={(e: ChangeEvent<HTMLInputElement>) => {
                const name = e.target.value;
                setCandidates((current) => current.map((c) => (c === candidate ? { ...c, name } : c)));
              }} />
            <button type="button" className={PRIMARY} disabled={!candidate.name.trim() || candidate.saving}
              onClick={() => void save(candidate)}>
              {candidate.saving ? "Saving…" : "Save Voice"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Clone Voice

function CloneVoice({ languages, onSaved }: { languages: string[]; onSaved: (voice: Voice) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [language, setLanguage] = useState("English");
  const [preview, setPreview] = useState<VoicePreview | null>(null);
  const [referenceUrl, setReferenceUrl] = useState<string | null>(null);
  const [sampleUrl, setSampleUrl] = useState<string | null>(null);
  const [transcript, setTranscript] = useState("");
  const [previewText, setPreviewText] = useState(EXAMPLE_TEXT);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toUrl = useAudioUrl();
  const current = useRef<VoicePreview | null>(null);
  current.current = preview;

  useEffect(() => () => {
    if (current.current) void cancelPreview(current.current.preview_id).catch(() => {});
  }, []);

  const upload = async () => {
    if (!file) return;
    setBusy("Checking the recording…");
    setError(null);
    try {
      if (preview) await cancelPreview(preview.preview_id).catch(() => {});   // adjusting the reference
      const uploaded = await uploadVoiceReference(file, { filename: file.name, consent, language });
      setPreview(uploaded);
      setSampleUrl(null);
      setTranscript(uploaded.suggested_transcript ?? "");
      setReferenceUrl(toUrl(await fetchPreviewAudio(uploaded.preview_id, "reference")));
    } catch (err) {
      setError(detail(err));
    } finally {
      setBusy(null);
    }
  };

  const sample = async () => {
    if (!preview) return;
    setBusy("Generating a cloned preview…");
    setError(null);
    try {
      const updated = await createCloneSample(preview.preview_id, { transcript, preview_text: previewText, language });
      setPreview(updated);
      setSampleUrl(toUrl(await fetchPreviewAudio(updated.preview_id, "sample")));
    } catch (err) {
      setError(detail(err));
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    if (!preview) return;
    setBusy("Saving…");
    setError(null);
    try {
      const voice = await saveVoice({ preview_id: preview.preview_id, name, transcript, language, consent: true });
      setPreview(null);
      setReferenceUrl(null);
      setSampleUrl(null);
      setFile(null);
      setName("");
      onSaved(voice);
    } catch (err) {
      setError(detail(err));
    } finally {
      setBusy(null);
    }
  };

  const discard = async () => {
    if (preview) await cancelPreview(preview.preview_id).catch(() => {});
    setPreview(null);
    setReferenceUrl(null);
    setSampleUrl(null);
  };

  const suggested = preview?.suggested_transcript;

  return (
    <div className="flex flex-col gap-3">
      <label className={LABEL}>
        Recording
        <input type="file" accept="audio/*,.wav,.mp3,.flac,.ogg,.opus,.m4a,.webm,.aac" disabled={!!busy}
          className="text-sm text-gray-300 file:mr-3 file:rounded-md file:border-0 file:bg-gray-700 file:px-3 file:py-1.5 file:text-gray-100"
          onChange={(e: ChangeEvent<HTMLInputElement>) => setFile(e.target.files?.[0] ?? null)} />
        <span className="text-xs font-normal text-gray-500">
          One speaker, little background noise, 3–30 seconds. Mono or stereo WAV, MP3, FLAC, OGG/Opus, M4A or WebM.
        </span>
      </label>
      <label className="flex items-start gap-2 text-sm text-gray-200">
        <input type="checkbox" className="mt-1" checked={consent} disabled={!!busy}
          onChange={(e) => setConsent(e.target.checked)} />
        I own this recording, or I have permission from the speaker to clone their voice.
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <LanguageSelect languages={languages} value={language} onChange={setLanguage} disabled={!!busy} />
        <button type="button" className={PRIMARY} disabled={!file || !consent || !!busy} onClick={() => void upload()}>
          {preview ? "Use This Recording Instead" : "Upload"}
        </button>
      </div>
      {preview && referenceUrl ? (
        <div className="flex flex-col gap-3 rounded-lg border border-gray-700 bg-gray-900/60 p-3">
          <div className="text-xs text-gray-400">
            Reference · {preview.reference_duration.toFixed(1)}s (normalized to 24 kHz mono)
          </div>
          <audio controls src={referenceUrl} className="w-full" />
          <label className={LABEL}>
            Reference Transcript
            <textarea rows={3} className={`resize-y ${INPUT}`} value={transcript} disabled={!!busy}
              placeholder="Exactly what is said in the recording"
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setTranscript(e.target.value)} />
            <span className="text-xs font-normal text-amber-300">
              {suggested
                ? "Suggested by speech recognition — listen and correct it; cloning quality depends on an exact transcript."
                : "Type exactly what is said in the recording."}
            </span>
          </label>
          <label className={LABEL}>
            Preview Text
            <textarea rows={2} className={`resize-y ${INPUT}`} value={previewText} disabled={!!busy}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPreviewText(e.target.value)} />
          </label>
          <div>
            <button type="button" className={BUTTON} disabled={!!busy || !transcript.trim() || !previewText.trim()}
              onClick={() => void sample()}>
              {preview.has_sample ? "Generate Another Preview" : "Generate Cloned Preview"}
            </button>
          </div>
          {sampleUrl ? <audio controls src={sampleUrl} className="w-full" /> : null}
          <div className="flex flex-wrap items-center gap-2">
            <input className={`${INPUT} max-w-xs`} placeholder="Voice name" value={name} disabled={!!busy}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setName(e.target.value)} />
            <button type="button" className={PRIMARY} disabled={!!busy || !name.trim() || !transcript.trim()}
              onClick={() => void save()}>Save Voice</button>
            <button type="button" className={BUTTON} disabled={!!busy} onClick={() => void discard()}>Discard</button>
          </div>
        </div>
      ) : null}
      {busy ? <p className="text-sm text-gray-400">{busy}</p> : null}
      {error ? <p role="alert" className="text-sm text-red-400">{error}</p> : null}
    </div>
  );
}

function LanguageSelect({ languages, value, onChange, disabled }: {
  languages: string[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-gray-300">
      Language
      <select className="rounded-md border border-gray-600 bg-gray-900 px-2 py-1.5 text-sm text-gray-100"
        value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {(languages.length ? languages : ["English"]).map((language) => (
          <option key={language} value={language}>{language}</option>
        ))}
      </select>
    </label>
  );
}

// ------------------------------------------------------------------ library

function VoiceRow({ voice, voices, sessionId, languages, onChanged, play }: {
  voice: Voice;
  voices: Voice[];
  sessionId: string | null;
  languages: string[];
  onChanged: () => void;
  play: (blob: Blob) => void;
}) {
  const [mode, setMode] = useState<"view" | "rename" | "edit" | "delete">("view");
  const [name, setName] = useState(voice.name);
  const [description, setDescription] = useState(voice.description ?? "");
  const [transcript, setTranscript] = useState(voice.reference_transcript);
  const [language, setLanguage] = useState(voice.language);
  const [replacement, setReplacement] = useState("");
  const [inUse, setInUse] = useState<VoiceApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(detail(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = () => run(async () => {
    try {
      await deleteVoice(voice.voice_id, replacement ? { replacementVoiceId: replacement, clearSessionOverrides: true } : {});
      announceLibraryChange();
      onChanged();
    } catch (err) {
      const apiError = err as VoiceApiError;
      if (apiError.status_code === 409) {
        setInUse(apiError);
        return;
      }
      throw err;
    }
  });

  const others = voices.filter((v) => v.voice_id !== voice.voice_id);
  const created = voice.created_at ? new Date(voice.created_at).toLocaleDateString() : "—";

  return (
    <li className="rounded-lg border border-gray-700 bg-gray-800/70 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium text-gray-100">
            {voice.name}
            {voice.is_builtin ? <span className="ml-2 text-xs text-gray-400">(built-in)</span> : null}
          </div>
          <div className="text-xs text-gray-400">
            {methodLabel(voice.method)} · {voice.language} · {created} · {voice.reference_duration.toFixed(1)}s reference
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={BUTTON} disabled={busy}
            onClick={() => void run(async () => play(await fetchVoiceAudio(voice.voice_id)))}>Reference</button>
          <button type="button" className={BUTTON} disabled={busy}
            onClick={() => void run(async () => play(await speakWithVoice(
              voice.voice_id, "Hello! This is how I sound in a conversation.")))}>Preview</button>
          <button type="button" className={BUTTON} disabled={busy || !sessionId}
            title={sessionId ? "Use this voice in the current conversation" : "Open a conversation first"}
            onClick={() => void run(async () => {
              if (!sessionId) return;
              await setSessionVoice(sessionId, voice.voice_id);
              getAvatarSyncBridge().publish(voiceSelectionEvent(sessionId, voice.voice_id));
              setMessage("Selected for the current conversation (from the next reply).");
            })}>Select</button>
          {!voice.is_builtin ? (
            <>
              <button type="button" className={BUTTON} onClick={() => setMode(mode === "rename" ? "view" : "rename")}>Rename</button>
              <button type="button" className={BUTTON} onClick={() => setMode(mode === "edit" ? "view" : "edit")}>Edit</button>
              <button type="button" className={DANGER} onClick={() => setMode(mode === "delete" ? "view" : "delete")}>Delete</button>
            </>
          ) : null}
        </div>
      </div>
      {voice.description ? <p className="mt-2 text-xs text-gray-400">{voice.description}</p> : null}
      {mode === "rename" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <input className={`${INPUT} max-w-xs`} value={name} onChange={(e) => setName(e.target.value)} />
          <button type="button" className={PRIMARY} disabled={busy || !name.trim()}
            onClick={() => void run(async () => {
              await updateVoice(voice.voice_id, { name });
              setMode("view");
              announceLibraryChange();
              onChanged();
            })}>Save Name</button>
        </div>
      ) : null}
      {mode === "edit" ? (
        <div className="mt-3 flex flex-col gap-2">
          <label className={LABEL}>
            Description
            <textarea rows={2} className={INPUT} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <label className={LABEL}>
            Reference Transcript
            <textarea rows={2} className={INPUT} value={transcript} onChange={(e) => setTranscript(e.target.value)} />
            <span className="text-xs font-normal text-gray-500">
              Must match the reference recording exactly; correcting it changes how the voice is cloned.
            </span>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <LanguageSelect languages={languages} value={language} onChange={setLanguage} />
            <button type="button" className={PRIMARY} disabled={busy || !transcript.trim()}
              onClick={() => void run(async () => {
                await updateVoice(voice.voice_id, { description, transcript, language });
                setMode("view");
                announceLibraryChange();
                onChanged();
              })}>Save Changes</button>
          </div>
        </div>
      ) : null}
      {mode === "delete" ? (
        <div className="mt-3 flex flex-col gap-2 rounded-md border border-red-800/50 bg-red-950/20 p-3 text-sm">
          {inUse ? (
            <>
              <p className="text-red-200">{inUseMessage(inUse)}</p>
              <label className="flex flex-wrap items-center gap-2 text-gray-200">
                Replace it with
                <select className="rounded-md border border-gray-600 bg-gray-900 px-2 py-1.5 text-sm"
                  value={replacement} onChange={(e) => setReplacement(e.target.value)}>
                  <option value="">Choose a voice…</option>
                  {others.map((v) => <option key={v.voice_id} value={v.voice_id}>{v.name}</option>)}
                </select>
              </label>
              <p className="text-xs text-gray-400">
                Companions and conversations using this voice will use the replacement instead.
              </p>
            </>
          ) : (
            <p className="text-gray-200">Delete “{voice.name}” and its reference recording?</p>
          )}
          <div className="flex gap-2">
            <button type="button" className={DANGER} disabled={busy || (!!inUse && !replacement)}
              onClick={() => void remove()}>Delete Voice</button>
            <button type="button" className={BUTTON} onClick={() => { setMode("view"); setInUse(null); }}>Cancel</button>
          </div>
        </div>
      ) : null}
      {message ? <p className="mt-2 text-xs text-emerald-300">{message}</p> : null}
      {error ? <p role="alert" className="mt-2 text-xs text-red-400">{error}</p> : null}
    </li>
  );
}

export function VoiceLibrary({ sessionId }: { sessionId: string | null }) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [languages, setLanguages] = useState<string[]>(["English"]);
  const [defaultReady, setDefaultReady] = useState(true);
  const [creating, setCreating] = useState<"design" | "clone" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [initializing, setInitializing] = useState(false);
  const toUrl = useAudioUrl();
  const player = useRef<HTMLAudioElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const library = await listVoices();
      setVoices(library.voices);
      setLanguages(library.languages);
      setDefaultReady(library.default_initialized);
      setError(null);
    } catch (err) {
      setError(detail(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const play = (blob: Blob) => {
    if (player.current) {
      player.current.src = toUrl(blob);
      void player.current.play();
    }
  };

  const saved = (voice: Voice) => {
    setNotice(`Saved “${voice.name}”.`);
    announceLibraryChange();
    void refresh();
  };

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-gray-100">Voice Library</h2>
            <p className="text-sm text-gray-400">
              Saved voices speak through Qwen3-TTS from their approved reference recording.
            </p>
          </div>
          <div className="flex gap-2" role="group" aria-label="Create Voice">
            <button type="button" className={creating === "design" ? PRIMARY : BUTTON}
              onClick={() => setCreating(creating === "design" ? null : "design")}>Design Voice</button>
            <button type="button" className={creating === "clone" ? PRIMARY : BUTTON}
              onClick={() => setCreating(creating === "clone" ? null : "clone")}>Clone Voice</button>
          </div>
        </div>

        {!defaultReady ? (
          <div className="rounded-lg border border-amber-600/50 bg-amber-950/30 p-3 text-sm text-amber-100">
            The built-in default voice has not been initialized, so conversations without a chosen voice
            cannot speak. An administrator can initialize it here, or run
            <code className="mx-1 rounded bg-gray-900 px-1">python -m chatbot.voices.init_default</code>.
            <div className="mt-2">
              <button type="button" className={BUTTON} disabled={initializing}
                onClick={() => void (async () => {
                  setInitializing(true);
                  try {
                    await initializeDefaultVoice();
                    announceLibraryChange();
                    await refresh();
                  } catch (err) {
                    setError(detail(err));
                  } finally {
                    setInitializing(false);
                  }
                })()}>
                {initializing ? "Initializing…" : "Initialize Default Voice"}
              </button>
            </div>
          </div>
        ) : null}

        {creating ? (
          <section className="rounded-lg border border-indigo-700/40 bg-gray-800/80 p-4">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">
              {creating === "design" ? "Design Voice" : "Clone Voice"}
            </h3>
            {creating === "design" ? (
              <DesignVoice languages={languages} onSaved={saved} />
            ) : (
              <CloneVoice languages={languages} onSaved={saved} />
            )}
          </section>
        ) : null}

        {notice ? <p className="text-sm text-emerald-300">{notice}</p> : null}
        {error ? <p role="alert" className="text-sm text-red-400">{error}</p> : null}
        <audio ref={player} className="hidden" />

        <ul className="flex flex-col gap-3" aria-label="Saved voices">
          {voices.map((voice) => (
            <VoiceRow key={voice.voice_id} voice={voice} voices={voices} sessionId={sessionId}
              languages={languages} onChanged={() => void refresh()} play={play} />
          ))}
          {voices.length === 0 ? <li className="text-sm text-gray-400">No saved voices yet.</li> : null}
        </ul>
      </div>
    </div>
  );
}

export default VoiceLibrary;
