import type { Message } from "../types";
import type { Companion, User } from "../api/client";

interface ChatMessageProps {
  message: Message;
  companion?: Companion | null;
  user?: User | null;
}

const AVATAR_COLORS = [
  "#4f46e5",
  "#0891b2",
  "#059669",
  "#7c3aed",
  "#db2777",
  "#ea580c",
  "#ca8a04",
  "#0d9488",
  "#2563eb",
  "#9333ea",
];

function avatarColorForName(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function getInitials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .map((part) => part[0]?.toUpperCase())
      .slice(0, 2)
      .join("") || "?"
  );
}

function MessageAvatar({
  isUser,
  companion,
  user,
}: {
  isUser: boolean;
  companion?: Companion | null;
  user?: User | null;
}) {
  const name = isUser
    ? user?.username || user?.email || "You"
    : companion?.name || "Assistant";

  if (!isUser && companion?.avatar_url) {
    return (
      <img
        src={companion.avatar_url}
        alt={`${companion.name} avatar`}
        className="h-8 w-8 shrink-0 rounded-full object-cover ring-1 ring-gray-700"
      />
    );
  }

  return (
    <span
      aria-label={`${name} avatar`}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ring-1 ring-gray-700"
      style={{ backgroundColor: avatarColorForName(name) }}
    >
      {getInitials(name)}
    </span>
  );
}

export function ChatMessage({ message, companion, user }: ChatMessageProps) {
  const isUser = message.role === "user";
  const alignmentClasses = isUser ? "items-end text-right" : "items-start";
  const bubbleClasses = isUser ? "bg-indigo-600" : "bg-gray-700";
  const rowClasses = isUser ? "flex-row-reverse" : "flex-row";

  return (
    <div className={`flex w-full gap-2 ${rowClasses}`}>
      <MessageAvatar isUser={isUser} companion={companion} user={user} />
      <div className={`flex min-w-0 flex-col ${alignmentClasses}`}>
        <div
          className={`max-w-[80%] rounded-lg px-4 py-2 text-sm leading-6 text-white sm:max-w-2xl ${bubbleClasses}`}
        >
          {message.content}
        </div>
        {message.timestamp ? (
          <time className="mt-1 text-xs text-gray-400" dateTime={message.timestamp}>
            {message.timestamp}
          </time>
        ) : null}
      </div>
    </div>
  );
}

export default ChatMessage;
