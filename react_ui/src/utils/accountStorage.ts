// Account-scoped view of a browser Storage.
//
// User-owned preferences (saved models, provider selection, TTS mode, companion
// auto-generation model) used to be stored under one global key per browser, so a
// second account on the same browser inherited the first account's choices. They
// are now stored under `<base>::<account id>`. Logging out never removes them, so
// logging back in restores them.
//
// Transition: a value stored under the old unscoped key predates scoping and
// belongs to whoever used this browser. It is adopted by the first account that
// reads it and the unscoped key is then removed, so it can never be adopted twice
// or leak to a second account.

export type BackingStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export interface AccountStorage extends BackingStorage {
  /** The concrete storage key for a preference, or null when no account is signed in. */
  keyFor(base: string): string | null;
}

const SEPARATOR = "::";

/** The account id (JWT `sub`) carried by an access token, or null if unreadable. */
export function accountIdFromToken(token: string | null | undefined): string | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded)) as { sub?: unknown };
    return typeof payload.sub === "string" && payload.sub ? payload.sub : null;
  } catch {
    return null;
  }
}

/**
 * Storage whose keys are private to `accountId`. With no account (or no backing
 * storage) reads return null and writes are ignored: a signed-out window never
 * shows or changes any account's preferences.
 */
export function accountStorage(
  backing: BackingStorage | null,
  accountId: string | null,
): AccountStorage {
  const keyFor = (base: string): string | null =>
    accountId ? `${base}${SEPARATOR}${accountId}` : null;

  return {
    keyFor,
    getItem(base: string): string | null {
      const key = keyFor(base);
      if (!backing || key === null) return null;
      const scoped = backing.getItem(key);
      if (scoped !== null) return scoped;
      const legacy = backing.getItem(base);
      if (legacy === null) return null;
      backing.setItem(key, legacy);
      backing.removeItem(base);
      return legacy;
    },
    setItem(base: string, value: string): void {
      const key = keyFor(base);
      if (backing && key !== null) backing.setItem(key, value);
    },
    removeItem(base: string): void {
      const key = keyFor(base);
      if (backing && key !== null) backing.removeItem(key);
    },
  };
}
