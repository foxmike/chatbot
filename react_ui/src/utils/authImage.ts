// Which image URLs need the signed-in user's bearer token to be fetched.
//
// Generated images are served by `GET /api/visual/images/{id}`, which now requires
// authentication and ownership, so an <img src> pointing at it cannot load by
// itself. Everything else (built-in portraits, data: URIs, external images) is
// intentionally shared and is rendered unchanged.

const PRIVATE_IMAGE_PATH = /^\/(?:api\/)?visual\/images\/[^/?#]+$/;

/** The backend-relative path of a private generated image, or null if `src` is not one. */
export function privateImagePath(src: string | null | undefined, origin?: string): string | null {
  if (!src || src.startsWith("data:") || src.startsWith("blob:")) return null;
  try {
    const url = new URL(src, origin ?? "http://localhost");
    if (/^[a-z][a-z0-9+.-]*:/i.test(src) && origin !== undefined && url.origin !== origin) {
      return null;
    }
    return PRIVATE_IMAGE_PATH.test(url.pathname) ? url.pathname : null;
  } catch {
    return null;
  }
}
