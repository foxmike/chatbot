import { useCallback, useEffect, useRef, useState } from "react";
import {
  dismissNotification,
  fetchNotifications,
  markNotificationRead,
} from "../api/client";
import type { NotificationItem } from "../api/client";
import type { ApiError } from "../types";

interface NotificationFeedProps {
  userId: string;
}

const POLL_INTERVAL_MS = 30000;

const typeIcon: Record<string, string> = {
  reminder: "\u23F0",
  emotional_checkin: "\u2764\uFE0F",
  continuation_prompt: "\u27A1\uFE0F",
  milestone: "\u2B50",
  event_callback: "\uD83D\uDD14",
};

const typeLabel: Record<string, string> = {
  reminder: "Reminder",
  emotional_checkin: "Check-in",
  continuation_prompt: "Nudge",
  milestone: "Milestone",
  event_callback: "Event",
};

function typeBadgeColor(notifType: string) {
  switch (notifType) {
    case "reminder":
      return "border-yellow-700 bg-yellow-900/40 text-yellow-200";
    case "emotional_checkin":
      return "border-pink-700 bg-pink-900/40 text-pink-200";
    case "continuation_prompt":
      return "border-blue-700 bg-blue-900/40 text-blue-200";
    case "milestone":
      return "border-green-700 bg-green-900/40 text-green-200";
    case "event_callback":
      return "border-purple-700 bg-purple-900/40 text-purple-200";
    default:
      return "border-gray-600 bg-gray-700 text-gray-300";
  }
}

function getErrorDetail(error: unknown) {
  if (
    typeof error === "object" &&
    error !== null &&
    "detail" in error &&
    typeof (error as ApiError).detail === "string"
  ) {
    return (error as ApiError).detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Something went wrong. Please try again.";
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffSec = Math.floor((now - then) / 1000);

  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function NotificationFeed({ userId }: NotificationFeedProps) {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);
  const lastSince = useRef<string | null>(null);

  const loadNotifications = useCallback(async () => {
    setError(null);

    try {
      const result = await fetchNotifications(
        userId,
        lastSince.current ?? undefined,
      );
      setNotifications(result.notifications);
      setPendingCount(result.pending_count);

      if (result.notifications.length > 0) {
        const timestamps = result.notifications
          .map((n) => n.created_at)
          .sort();
        lastSince.current = timestamps[timestamps.length - 1];
      }
    } catch (err) {
      console.error("Failed to load notifications", err);
      setError(getErrorDetail(err));
    } finally {
      setIsFetching(false);
    }
  }, [userId]);

  useEffect(() => {
    let isMounted = true;

    async function initialLoad() {
      setIsFetching(true);
      setError(null);

      try {
        const result = await fetchNotifications(userId);
        if (isMounted) {
          setNotifications(result.notifications);
          setPendingCount(result.pending_count);

          if (result.notifications.length > 0) {
            const timestamps = result.notifications
              .map((n) => n.created_at)
              .sort();
            lastSince.current = timestamps[timestamps.length - 1];
          }
        }
      } catch (err) {
        console.error("Failed to load notifications", err);
        if (isMounted) {
          setError(getErrorDetail(err));
        }
      } finally {
        if (isMounted) {
          setIsFetching(false);
        }
      }
    }

    initialLoad();

    const intervalId = setInterval(() => {
      loadNotifications();
    }, POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      clearInterval(intervalId);
    };
  }, [userId, loadNotifications]);

  const handleMarkRead = async (notification: NotificationItem) => {
    if (notification.read) return;

    setActionError(null);
    setActingId(notification.notification_id);

    try {
      await markNotificationRead(notification.notification_id);
      setNotifications((prev) =>
        prev.map((n) =>
          n.notification_id === notification.notification_id
            ? { ...n, read: true }
            : n,
        ),
      );
    } catch (err) {
      setActionError(getErrorDetail(err));
    } finally {
      setActingId(null);
    }
  };

  const handleDismiss = async (notification: NotificationItem) => {
    setActionError(null);
    setActingId(notification.notification_id);

    try {
      await dismissNotification(notification.notification_id);
      setNotifications((prev) =>
        prev.filter((n) => n.notification_id !== notification.notification_id),
      );
      setPendingCount((c) => Math.max(0, c - 1));
    } catch (err) {
      setActionError(getErrorDetail(err));
    } finally {
      setActingId(null);
    }
  };

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-100">
              Notifications
            </h2>
            <p className="mt-1 text-sm text-gray-400">
              Recent updates and check-ins from your companion.
            </p>
          </div>

          {pendingCount > 0 ? (
            <span className="inline-flex items-center rounded-full border border-indigo-700 bg-indigo-900/40 px-3 py-1 text-xs font-semibold text-indigo-200">
              {pendingCount} pending
            </span>
          ) : null}
        </div>

        {error ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        {actionError ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {actionError}
            <button
              type="button"
              className="ml-2 underline hover:no-underline"
              onClick={() => setActionError(null)}
            >
              Dismiss
            </button>
          </div>
        ) : null}

        {isFetching ? (
          <div className="text-sm text-gray-400">Loading notifications...</div>
        ) : notifications.length === 0 ? (
          <div className="text-sm text-gray-500">No notifications.</div>
        ) : (
          <ul className="flex max-h-96 flex-col gap-2 overflow-y-auto">
            {notifications.map((notification) => {
              const icon = typeIcon[notification.notif_type] ?? "\uD83D\uDCE8";
              const label =
                typeLabel[notification.notif_type] ?? notification.notif_type;

              return (
                <li key={notification.notification_id}>
                  <button
                    type="button"
                    disabled={actingId === notification.notification_id}
                    className="flex w-full items-start gap-3 rounded-lg border border-gray-700 bg-gray-900/60 p-4 text-left transition hover:border-gray-600 hover:bg-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-wait disabled:opacity-60"
                    onClick={() => handleMarkRead(notification)}
                  >
                    <span
                      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-sm ${typeBadgeColor(notification.notif_type)}`}
                    >
                      {icon}
                    </span>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={`rounded-full border px-2 py-0.5 text-xs font-medium ${typeBadgeColor(notification.notif_type)}`}
                        >
                          {label}
                        </span>
                        {!notification.read ? (
                          <span className="h-2 w-2 shrink-0 rounded-full bg-indigo-400" />
                        ) : null}
                      </div>
                      <h3 className="mt-1 text-sm font-semibold text-gray-100">
                        {notification.title}
                      </h3>
                      <p className="mt-1 text-sm leading-6 text-gray-400">
                        {notification.body}
                      </p>
                      <time className="mt-2 block text-xs text-gray-500">
                        {relativeTime(notification.created_at)}
                      </time>
                    </div>

                    <button
                      type="button"
                      disabled={actingId === notification.notification_id}
                      className="shrink-0 rounded border border-gray-600 px-2 py-1 text-xs text-gray-400 transition hover:border-red-500 hover:text-red-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDismiss(notification);
                      }}
                      title="Dismiss"
                    >
                      X
                    </button>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

export default NotificationFeed;