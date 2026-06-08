import type { Message } from "../types";
import type { Companion, User } from "../api/client";
import {
  avatarColorForCompanion,
  avatarColorForKey,
  getInitials,
} from "../utils/avatar";

interface ChatMessageProps {
  message: Message;
  companion?: Companion | null;
  user?: User | null;
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
      style={{
        backgroundColor:
          !isUser && companion
            ? avatarColorForCompanion(companion)
            : avatarColorForKey(name),
      }}
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
