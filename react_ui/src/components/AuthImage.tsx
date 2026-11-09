import { forwardRef, useEffect, useState } from "react";
import type { ImgHTMLAttributes } from "react";
import { privateImageObjectUrl } from "../api/images";
import { privateImagePath } from "../utils/authImage";

// Transparent pixel shown while a private image loads, or if it cannot be loaded
// (not owned, deleted, or the session ended) - never another account's picture.
const BLANK =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

/**
 * Drop-in <img>. Private generated images (/api/visual/images/{id}) are fetched with
 * the user's bearer token; every other source (built-in portraits, data: URIs,
 * external images) renders exactly as a plain <img>.
 */
export const AuthImage = forwardRef<HTMLImageElement, ImgHTMLAttributes<HTMLImageElement>>(
  function AuthImage({ src, ...rest }, ref) {
    const path = privateImagePath(src, window.location.origin);
    const [resolved, setResolved] = useState<{ path: string; url: string } | null>(null);

    useEffect(() => {
      if (!path) {
        setResolved(null);
        return;
      }
      let cancelled = false;
      privateImageObjectUrl(path).then(
        (url) => {
          if (!cancelled) setResolved({ path, url });
        },
        () => {
          if (!cancelled) setResolved(null);
        },
      );
      return () => {
        cancelled = true;
      };
    }, [path]);

    const shown = path ? (resolved?.path === path ? resolved.url : BLANK) : src;
    return <img {...rest} ref={ref} src={shown} />;
  },
);

export default AuthImage;
