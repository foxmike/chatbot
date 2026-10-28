/**
 * Live assistant text of a spoken turn, shown in the main chat while it is still being generated.
 *
 * A tiny external store instead of React state in App: tokens arrive many times a second, and
 * only the bubble that displays them should re-render. Notifications are batched to one per
 * animation frame; the text itself is never delayed or dropped.
 */
let text: string | null = null; // null = no spoken turn in progress
let frame: number | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  frame = null;
  listeners.forEach((listener) => listener());
}

function schedule(): void {
  if (frame !== null) return;
  if (typeof requestAnimationFrame === "function") {
    frame = requestAnimationFrame(notify);
  } else {
    notify();
  }
}

export const liveAssistantText = {
  get: (): string | null => text,
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  /** Start a turn (empty text) or clear it (null). Takes effect immediately. */
  reset(next: string | null): void {
    text = next;
    if (frame !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame);
    notify();
  },
  /** Append streamed text; subscribers are notified at most once per frame. */
  append(delta: string): void {
    if (text === null) text = "";
    text += delta;
    schedule();
  },
};
