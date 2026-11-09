// Private generated images are fetched with the signed-in user's bearer token (the
// existing authorized client) and shown through blob URLs, so no token ever appears
// in an image URL. Blob URLs are cached per session scope and revoked when the
// session ends, so one account's private images cannot outlive it in the page.
import { authorizedFetch, onSessionEnd } from "./client.ts";

const cache = new Map<string, Promise<string>>();

onSessionEnd(() => {
  for (const pending of cache.values()) {
    pending.then(
      (url) => URL.revokeObjectURL(url),
      () => undefined,
    );
  }
  cache.clear();
});

export function privateImageObjectUrl(path: string): Promise<string> {
  const cached = cache.get(path);
  if (cached) return cached;

  const pending: Promise<string> = authorizedFetch(path)
    .then((response) => response.blob())
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      if (cache.get(path) !== pending) {
        // The session ended while the image was downloading.
        URL.revokeObjectURL(url);
        throw new Error("Image discarded: the session that requested it has ended");
      }
      return url;
    });
  cache.set(path, pending);
  pending.catch(() => {
    if (cache.get(path) === pending) cache.delete(path);
  });
  return pending;
}
