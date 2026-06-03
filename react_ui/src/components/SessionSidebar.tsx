import type { Session } from "../types";

interface SessionSidebarProps {
  sessions: Session[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onNewSession: () => void;
}

function formatCreatedAt(createdAt: string) {
  return createdAt.slice(0, 10);
}

export function SessionSidebar({
  sessions,
  activeSessionId,
  onSelect,
  onNewSession,
}: SessionSidebarProps) {
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-gray-700 bg-gray-800 text-gray-100">
      <div className="border-b border-gray-700 p-3">
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded-lg border border-gray-600 px-3 py-2 text-sm font-medium text-gray-100 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
          onClick={onNewSession}
        >
          <svg
            className="h-4 w-4"
            viewBox="0 0 20 20"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
          >
            <path
              d="M10 4V16M4 10H16"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
          New Chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {sessions.length === 0 ? (
          <div className="px-3 py-4 text-sm text-gray-500">No conversations yet</div>
        ) : (
          <nav className="flex flex-col gap-1" aria-label="Conversations">
            {sessions.map((session) => {
              const isActive = session.session_id === activeSessionId;

              return (
                <button
                  key={session.session_id}
                  type="button"
                  className={`border-l-4 px-3 py-2 text-left transition focus:outline-none focus:ring-2 focus:ring-indigo-500/60 ${
                    isActive
                      ? "border-indigo-500 bg-gray-700"
                      : "border-transparent hover:bg-gray-700/60"
                  }`}
                  aria-current={isActive ? "page" : undefined}
                  onClick={() => onSelect(session.session_id)}
                >
                  <span className="block truncate text-sm font-medium text-gray-100">
                    {session.title}
                  </span>
                  <time
                    className="mt-1 block truncate text-xs text-gray-400"
                    dateTime={session.created_at}
                  >
                    {formatCreatedAt(session.created_at)}
                  </time>
                </button>
              );
            })}
          </nav>
        )}
      </div>
    </aside>
  );
}

export default SessionSidebar;
