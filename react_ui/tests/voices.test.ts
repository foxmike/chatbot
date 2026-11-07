import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  COMPANION_DEFAULT,
  VOICE_LIBRARY_EVENT,
  VOICE_SELECTION_EVENT,
  companionVoiceOptions,
  inUseMessage,
  isSelectableValue,
  selectedVoiceValue,
  sessionVoiceOptions,
  shouldRefreshSelection,
  voiceIdForValue,
  voiceSelectionEvent,
} from "../src/utils/voices.ts";
import type { SessionVoice, Voice } from "../src/api/voices.ts";

function voice(id: string, name: string, extra: Partial<Voice> = {}): Voice {
  return {
    voice_id: id, name, method: "designed", description: null, reference_transcript: "words",
    language: "English", model_family: "qwen3-tts-12hz", creation_model: "VoiceDesign",
    synthesis_model: "Base", reference_duration: 5, reference_sample_rate: 24000,
    reference_sha256: "x", status: "ready", is_builtin: false, created_at: "2026-11-06T00:00:00Z",
    updated_at: null, metadata: {}, ...extra,
  };
}

const DEFAULT = voice("default-id", "Default (built-in)", { is_builtin: true });
const ROSE = voice("rose-id", "Rose");
const ROSE_TWO = voice("rose-2-id", "Rose", { method: "cloned" });   // same name, different voice
const MAX = voice("max-id", "Max", { method: "cloned" });
const VOICES = [MAX, ROSE, DEFAULT, ROSE_TWO];

function selection(extra: Partial<SessionVoice> = {}): SessionVoice {
  return {
    session_id: "s1", override_voice_id: null, companion_id: "c1", companion_voice_id: null,
    default_voice_id: "default-id", effective_voice_id: "default-id", effective_source: "default",
    error: null, ...extra,
  };
}

// ---------------------------------------------------------------- options

test("chat/popup options: companion default first, then voices by name with ids as values", () => {
  const options = sessionVoiceOptions(VOICES, selection({ companion_voice_id: "max-id" }));
  assert.deepEqual(options[0], { value: COMPANION_DEFAULT, label: "Companion default (Max)" });
  assert.deepEqual(options.slice(1).map((o) => o.label), ["Default (built-in)", "Max", "Rose", "Rose"]);
  // Two voices named "Rose" stay two distinct choices: identity is the id, never the name.
  assert.deepEqual(options.filter((o) => o.label === "Rose").map((o) => o.value).sort(), ["rose-2-id", "rose-id"]);
});

test("without a companion voice the inherited choice names the built-in default", () => {
  assert.equal(sessionVoiceOptions(VOICES, selection())[0].label, "Companion default (Default (built-in))");
  assert.equal(sessionVoiceOptions([], null)[0].label, "Companion default");
});

test("the dropdown shows the conversation override, or the companion default", () => {
  assert.equal(selectedVoiceValue(selection()), COMPANION_DEFAULT);
  assert.equal(selectedVoiceValue(selection({ override_voice_id: "rose-id" })), "rose-id");
  assert.equal(voiceIdForValue(COMPANION_DEFAULT), null);
  assert.equal(voiceIdForValue("rose-id"), "rose-id");
});

test("ids that are not the user's voices (or were deleted) cannot be selected", () => {
  assert.equal(isSelectableValue(VOICES, "rose-id"), true);
  assert.equal(isSelectableValue(VOICES, COMPANION_DEFAULT), true);
  assert.equal(isSelectableValue(VOICES, "deleted-id"), false);
  assert.equal(isSelectableValue(VOICES, "Rose"), false);   // a name is not an id
});

test("companion Default Voice options list the user's own voices plus the built-in default", () => {
  const options = companionVoiceOptions(VOICES);
  assert.deepEqual(options[0], { value: COMPANION_DEFAULT, label: "Built-in default" });
  assert.deepEqual(options.slice(1).map((o) => o.value), ["max-id", "rose-id", "rose-2-id"]);
});

// ---------------------------------------------------------------- synchronization

test("a selection made in one window makes the other window re-read the same conversation", () => {
  const event = voiceSelectionEvent("s1", "rose-id");
  assert.deepEqual(event, { type: VOICE_SELECTION_EVENT, sessionId: "s1", voiceId: "rose-id" });
  assert.equal(shouldRefreshSelection(event, "s1"), true);
  assert.equal(shouldRefreshSelection(event, "s2"), false);      // another conversation is unaffected
  assert.equal(shouldRefreshSelection(event, null), false);
  assert.equal(shouldRefreshSelection({ type: VOICE_LIBRARY_EVENT }, "s2"), true);
  assert.equal(shouldRefreshSelection({ type: "AVATAR_CONNECTED" }, "s1"), false);
});

// ---------------------------------------------------------------- requests

interface Captured {
  url: string;
  method: string;
  body: unknown;
  headers: Record<string, string>;
}

async function loadApi() {
  const store = new Map<string, string>([["auth_token", "tok"]]);
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  };
  const client = await import("../src/api/client.ts");
  client.setAccessToken("tok");
  return await import("../src/api/voices.ts");
}

function stubFetch(reply: (call: Captured) => Response) {
  const calls: Captured[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Captured = {
      url: String(input),
      method: init?.method ?? "GET",
      body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    return reply(call);
  }) as typeof fetch;
  return { calls, restore: () => void (globalThis.fetch = original) };
}

const ok = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

test("choosing a voice in chat sends its id for the active conversation", async () => {
  const api = await loadApi();
  const net = stubFetch((call) => ok(selection({ override_voice_id: (call.body as { voice_id: string }).voice_id })));
  try {
    const result = await api.setSessionVoice("s1", voiceIdForValue("rose-id"));
    assert.equal(result.override_voice_id, "rose-id");
  } finally {
    net.restore();
  }
  assert.equal(net.calls.length, 1);
  assert.equal(net.calls[0].url, "/api/voices/sessions/s1");
  assert.equal(net.calls[0].method, "PUT");
  assert.deepEqual(net.calls[0].body, { voice_id: "rose-id" });
  assert.equal(net.calls[0].headers["Authorization"], "Bearer tok");
});

test("returning to the companion default sends null, not a name or another voice", async () => {
  const api = await loadApi();
  const net = stubFetch(() => ok(selection()));
  try {
    await api.setSessionVoice("s1", voiceIdForValue(COMPANION_DEFAULT));
  } finally {
    net.restore();
  }
  assert.deepEqual(net.calls[0].body, { voice_id: null });
});

test("chat and popup control the same backend selection and stay in sync", async () => {
  const api = await loadApi();
  let stored: string | null = null;
  const net = stubFetch((call) => {
    if (call.method === "PUT") stored = (call.body as { voice_id: string | null }).voice_id;
    return ok(selection({ override_voice_id: stored }));
  });
  try {
    // The popup changes the voice and broadcasts; the chat window receives the event...
    await api.setSessionVoice("s1", "max-id");
    const event = voiceSelectionEvent("s1", "max-id");
    assert.equal(shouldRefreshSelection(event, "s1"), true);
    // ...and re-reads the one selection from the backend: both now show the same voice.
    const inChat = await api.getSessionVoice("s1");
    assert.equal(selectedVoiceValue(inChat), "max-id");
    // And the other way round.
    await api.setSessionVoice("s1", "rose-id");
    assert.equal(selectedVoiceValue(await api.getSessionVoice("s1")), "rose-id");
  } finally {
    net.restore();
  }
  assert.deepEqual(net.calls.map((c) => [c.method, c.url]), [
    ["PUT", "/api/voices/sessions/s1"], ["GET", "/api/voices/sessions/s1"],
    ["PUT", "/api/voices/sessions/s1"], ["GET", "/api/voices/sessions/s1"],
  ]);
});

test("uploads are multipart with consent, filename and language (no JSON content type)", async () => {
  const api = await loadApi();
  const net = stubFetch(() => ok({ preview_id: "p1", kind: "clone" }, 201));
  try {
    await api.uploadVoiceReference(new Blob([new Uint8Array([1, 2, 3])], { type: "audio/wav" }), {
      filename: "me.wav", consent: true, language: "French",
    });
  } finally {
    net.restore();
  }
  const form = net.calls[0].body as FormData;
  assert.ok(form instanceof FormData);
  assert.equal(form.get("consent"), "true");
  assert.equal(form.get("language"), "French");
  assert.equal((form.get("file") as File).name, "me.wav");
  assert.equal(net.calls[0].headers["Content-Type"], undefined);
  assert.equal(net.calls[0].url, "/api/voices/references");
});

test("saving an approved preview references it by preview id", async () => {
  const api = await loadApi();
  const net = stubFetch(() => ok(ROSE, 201));
  try {
    await api.saveVoice({ preview_id: "p1", name: "Rose" });
  } finally {
    net.restore();
  }
  assert.deepEqual(net.calls[0].body, { preview_id: "p1", name: "Rose" });
  assert.equal(net.calls[0].url, "/api/voices");
});

test("deleting a voice in use reports which companions and conversations use it", async () => {
  const api = await loadApi();
  const net = stubFetch(() => ok({
    detail: "Voice 'Rose' is in use; reassign it or choose a replacement voice",
    companions: [{ companion_id: "c1", name: "Ava" }],
    sessions: [{ session_id: "s1", title: "chat" }],
  }, 409));
  let caught: unknown;
  try {
    await api.deleteVoice("rose-id");
  } catch (error) {
    caught = error;
  } finally {
    net.restore();
  }
  const error = caught as { status_code: number; companions: unknown[] };
  assert.equal(error.status_code, 409);
  assert.match(inUseMessage(caught as never), /companions: Ava; 1 conversation\)/);
});

test("deleting with an explicit replacement passes it to the backend", async () => {
  const api = await loadApi();
  const net = stubFetch(() => ok({ voice_id: "rose-id" }));
  try {
    await api.deleteVoice("rose-id", { replacementVoiceId: "max-id" });
  } finally {
    net.restore();
  }
  assert.equal(net.calls[0].url, "/api/voices/rose-id?replacement_voice_id=max-id");
  assert.equal(net.calls[0].method, "DELETE");
});

// ---------------------------------------------------------------- wiring

test("both the chat controls and the avatar popup render the same Voice selector", () => {
  const chat = readFileSync(new URL("../src/components/ChatInput.tsx", import.meta.url), "utf8");
  const popup = readFileSync(new URL("../src/components/AvatarPopup.tsx", import.meta.url), "utf8");
  const selector = readFileSync(new URL("../src/components/VoiceSelector.tsx", import.meta.url), "utf8");
  assert.match(chat, /<VoiceSelector sessionId=\{sessionId\} \/>/);
  assert.match(popup, /<VoiceSelector sessionId=\{sessionId\} variant="popup" \/>/);
  // TTS (how text is chunked) stays a separate control from Voice (who speaks).
  assert.match(popup, /id="tts-mode"/);
  // One source of truth: the backend selection, re-read on sync events; nothing in localStorage.
  assert.match(selector, /getSessionVoice\(sessionId\)/);
  assert.match(selector, /publish\(voiceSelectionEvent/);
  assert.doesNotMatch(selector, /localStorage/);
  assert.match(selector, /Voice:/);
});

test("the companion editor stores a voice id and the app has a Voice Library tab", () => {
  const form = readFileSync(new URL("../src/components/CompanionForm.tsx", import.meta.url), "utf8");
  const shell = readFileSync(new URL("../src/components/AppShell.tsx", import.meta.url), "utf8");
  assert.match(form, /Default Voice/);
  assert.match(form, /voice_id: voiceIdForValue\(form\.voice_id\)/);
  assert.match(shell, /\{ key: "voices", label: "Voice Library" \}/);
});
