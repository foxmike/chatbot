import test from "node:test";
import assert from "node:assert/strict";
import { accountIdFromToken, accountStorage } from "../src/utils/accountStorage.ts";
import { privateImagePath } from "../src/utils/authImage.ts";
import {
  BUILT_IN_MODELS,
  STORAGE_KEY,
  addModel,
  loadPickerState,
  routingFromStorage,
  selectModel,
  serializeState,
} from "../src/utils/modelPicker.ts";
import { DEFAULT_TTS_MODE, loadTtsMode, saveTtsMode } from "../src/avatar/ttsMode.ts";

// ---- helpers ---------------------------------------------------------------

function fakeJwt(sub: string): string {
  const b64 = (o: object) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub, username: sub })}.signature`;
}

function memoryStorage() {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (k: string) => (map.has(k) ? (map.get(k) as string) : null),
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  };
}

const A = "user-a";
const B = "user-b";

// ---- account id --------------------------------------------------------------

test("the account id is the token subject; unreadable tokens have none", () => {
  assert.equal(accountIdFromToken(fakeJwt(A)), A);
  assert.equal(accountIdFromToken(null), null);
  assert.equal(accountIdFromToken("not-a-jwt"), null);
  assert.equal(accountIdFromToken("a.b!!.c"), null);
});

// ---- saved models / provider selection ------------------------------------

test("saved models are account-specific, survive logout, and new accounts get defaults", () => {
  const disk = memoryStorage();

  // Account A saves models X and Y and selects X.
  let a = loadPickerState(accountStorage(disk, A));
  a = addModel(a, "vendor/model-x");
  a = addModel(a, "vendor/model-y");
  a = selectModel(a, "vendor/model-x");
  accountStorage(disk, A).setItem(STORAGE_KEY, serializeState(a));

  // Account B has none of that: system defaults only.
  const freshB = loadPickerState(accountStorage(disk, B));
  assert.deepEqual(freshB.models.openrouter, BUILT_IN_MODELS.openrouter);

  // B saves Z and W.
  let b = addModel(addModel(freshB, "vendor/model-z"), "vendor/model-w");
  accountStorage(disk, B).setItem(STORAGE_KEY, serializeState(b));

  // Logout/login (a new adapter over the same browser storage) restores each list.
  const restoredA = loadPickerState(accountStorage(disk, A));
  assert.ok(restoredA.models.openrouter.includes("vendor/model-x"));
  assert.ok(restoredA.models.openrouter.includes("vendor/model-y"));
  assert.ok(!restoredA.models.openrouter.includes("vendor/model-z"));
  assert.equal(restoredA.model, "vendor/model-x");

  const restoredB = loadPickerState(accountStorage(disk, B));
  assert.ok(restoredB.models.openrouter.includes("vendor/model-z"));
  assert.ok(restoredB.models.openrouter.includes("vendor/model-w"));
  assert.ok(!restoredB.models.openrouter.includes("vendor/model-x"));
});

test("the avatar window's routing follows the signed-in account's selection", () => {
  const disk = memoryStorage();
  const a = selectModel(addModel(loadPickerState(accountStorage(disk, A)), "vendor/only-a"), "vendor/only-a");
  accountStorage(disk, A).setItem(STORAGE_KEY, serializeState(a));

  assert.equal(
    routingFromStorage(accountStorage(disk, A).getItem(STORAGE_KEY)).model_override,
    "vendor/only-a",
  );
  assert.notEqual(
    routingFromStorage(accountStorage(disk, B).getItem(STORAGE_KEY)).model_override,
    "vendor/only-a",
  );
});

// ---- TTS mode ----------------------------------------------------------------

test("TTS mode is account-specific and persists for the right account", () => {
  const disk = memoryStorage();
  assert.equal(saveTtsMode(accountStorage(disk, A), "fast"), true);

  assert.equal(loadTtsMode(accountStorage(disk, A)), "fast");
  assert.equal(loadTtsMode(accountStorage(disk, B)), DEFAULT_TTS_MODE);
});

// ---- storage transition and signed-out behaviour -------------------------------

test("a pre-scoping value is adopted once by the first account and never leaks to the next", () => {
  const disk = memoryStorage();
  disk.setItem("avatar_tts_mode", "fast"); // written by the old, unscoped code

  assert.equal(loadTtsMode(accountStorage(disk, A)), "fast"); // preserved for its owner
  assert.equal(disk.getItem("avatar_tts_mode"), null); // legacy key consumed
  assert.equal(loadTtsMode(accountStorage(disk, B)), DEFAULT_TTS_MODE); // not inherited
  assert.equal(loadTtsMode(accountStorage(disk, A)), "fast"); // A still has it
});

test("a signed-out window neither reads nor writes any account's preferences", () => {
  const disk = memoryStorage();
  saveTtsMode(accountStorage(disk, A), "fast");
  const signedOut = accountStorage(disk, null);

  assert.equal(signedOut.getItem("avatar_tts_mode"), null);
  assert.equal(signedOut.keyFor("avatar_tts_mode"), null);
  signedOut.setItem("avatar_tts_mode", "natural");
  signedOut.removeItem("avatar_tts_mode");
  assert.equal(loadTtsMode(accountStorage(disk, A)), "fast");
  assert.equal(disk.map.size, 1);
});

// ---- private images --------------------------------------------------------------

test("only generated-image URLs need authentication; shared images render unchanged", () => {
  const origin = "https://chat.example.test";
  assert.equal(privateImagePath("/api/visual/images/img_123", origin), "/api/visual/images/img_123");
  assert.equal(privateImagePath(`${origin}/api/visual/images/img_123`, origin), "/api/visual/images/img_123");
  assert.equal(privateImagePath("/portraits/elena.jpg", origin), null);
  assert.equal(privateImagePath("data:image/png;base64,AAAA", origin), null);
  assert.equal(privateImagePath("blob:https://chat.example.test/x", origin), null);
  assert.equal(privateImagePath("https://elsewhere.test/api/visual/images/img_123", origin), null);
  assert.equal(privateImagePath("/api/visual/images/img_123/extra", origin), null);
  assert.equal(privateImagePath(null, origin), null);
});

// ---- API client: logout, session scope, 401 ------------------------------------------

type Call = { url: string; method: string; authorization: string | null; signal?: AbortSignal | null };

const disk = memoryStorage();
Object.defineProperty(globalThis, "localStorage", { value: disk, configurable: true });
const calls: Call[] = [];
let respond: (url: string, init: RequestInit) => Promise<Response> | Response = () =>
  new Response("{}", { status: 200 });
globalThis.fetch = (async (input: unknown, init: RequestInit = {}) => {
  const headers = (init.headers ?? {}) as Record<string, string>;
  calls.push({
    url: String(input),
    method: init.method ?? "GET",
    authorization: headers["Authorization"] ?? null,
    signal: init.signal,
  });
  return respond(String(input), init);
}) as typeof fetch;

const client = await import("../src/api/client.ts");
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("logout revokes the server session with the token still present, then clears credentials", async () => {
  calls.length = 0;
  client.setAccessToken(fakeJwt(A));
  let tokenWhenServerCalled: string | null = null;
  respond = () => {
    tokenWhenServerCalled = client.getAccessToken();
    return json({ detail: "Logged out" });
  };
  let sessionEnded = 0;
  client.onSessionEnd(() => void (sessionEnded += 1));

  // A preference saved while signed in must outlive logout.
  client.accountLocalStorage().setItem("avatar_tts_mode", "fast");

  await client.logout();

  assert.equal(calls[0].method, "POST");
  assert.ok(calls[0].url.endsWith("/api/auth/logout"));
  assert.equal(calls[0].authorization, `Bearer ${fakeJwt(A)}`);
  assert.equal(tokenWhenServerCalled, fakeJwt(A));
  assert.equal(client.getAccessToken(), null);
  assert.equal(disk.getItem("auth_token"), null);
  assert.equal(sessionEnded >= 1, true);

  // Back in as A: the preference is still there. B never sees it.
  client.setAccessToken(fakeJwt(A));
  assert.equal(client.accountLocalStorage().getItem("avatar_tts_mode"), "fast");
  client.setAccessToken(fakeJwt(B));
  assert.equal(client.accountLocalStorage().getItem("avatar_tts_mode"), null);
  client.clearAccessToken();
});

test("logout still signs out locally when the server cannot be reached", async () => {
  client.setAccessToken(fakeJwt(A));
  respond = () => json({ detail: "boom" }, 500);
  await assert.rejects(client.logout());
  assert.equal(client.getAccessToken(), null);
  assert.equal(disk.getItem("auth_token"), null);
});

test("logging out an already revoked session succeeds", async () => {
  client.setAccessToken(fakeJwt(A));
  respond = () => json({ detail: "Invalid or expired token" }, 401);
  await client.logout();
  assert.equal(client.getAccessToken(), null);
});

test("a response arriving after the session ended is discarded, not delivered", async () => {
  client.setAccessToken(fakeJwt(A));
  let release: (r: Response) => void = () => undefined;
  respond = () => new Promise<Response>((resolve) => (release = resolve));
  const pending = client.listRooms("user-a");
  const outcome = assert.rejects(pending, client.StaleSessionError);

  client.endSessionScope(); // account A signs out / B signs in
  release(json([{ room_id: "a-private-room" }]));
  await outcome;
  client.clearAccessToken();
});

test("ending the session aborts the account's in-flight requests", async () => {
  client.setAccessToken(fakeJwt(A));
  calls.length = 0;
  respond = (_url, init) =>
    new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  const pending = client.listRooms("user-a");
  const outcome = assert.rejects(pending, { name: "AbortError" });
  client.endSessionScope();
  await outcome;
  assert.equal(calls[0].signal?.aborted, true);
  client.clearAccessToken();
});

test("signing in as a different account ends the previous account's session scope", () => {
  client.setAccessToken(fakeJwt(A));
  let ended = 0;
  const off = client.onSessionEnd(() => void (ended += 1));
  client.setAccessToken(fakeJwt(A)); // same account: nothing ends
  assert.equal(ended, 0);
  client.setAccessToken(fakeJwt(B)); // different account
  assert.equal(ended, 1);
  off();
  client.clearAccessToken();
});

test("each account's requests carry its own token and the room list is requested for it", async () => {
  calls.length = 0;
  respond = () => json([]);
  client.setAccessToken(fakeJwt(A));
  await client.listRooms(A);
  client.setAccessToken(fakeJwt(B));
  await client.listRooms(B);

  assert.ok(calls[0].url.endsWith(`/api/rooms/${A}`));
  assert.equal(calls[0].authorization, `Bearer ${fakeJwt(A)}`);
  assert.ok(calls[1].url.endsWith(`/api/rooms/${B}`));
  assert.equal(calls[1].authorization, `Bearer ${fakeJwt(B)}`);
  client.clearAccessToken();
});

test("an expired or revoked session (401) signs out once and notifies the app", async () => {
  client.setAccessToken(fakeJwt(A));
  let notified = 0;
  client.setUnauthorizedHandler(() => void (notified += 1));
  respond = () => json({ detail: "Invalid or expired token" }, 401);

  await assert.rejects(client.listRooms(A), { status_code: 401 });
  assert.equal(notified, 1);
  assert.equal(client.getAccessToken(), null);

  // Wrong credentials at login are not an expired session.
  respond = () => json({ detail: "Invalid credentials" }, 401);
  await assert.rejects(client.login("a@example.test", "bad"), { status_code: 401 });
  assert.equal(notified, 1);
  client.setUnauthorizedHandler(null);
});
