import type { Message } from "../types";

interface ChatMessageProps {
  message: Message;
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  const alignmentClasses = isUser ? "items-end" : "items-start";
  const bubbleClasses = isUser ? "bg-indigo-600" : "bg-gray-700";

  return (
    <div className={`flex w-full flex-col ${alignmentClasses}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2 text-sm leading-6 text-white ${bubbleClasses}`}
      >
        {message.content}
      </div>
      {message.timestamp ? (
        <time className="mt-1 text-xs text-gray-400" dateTime={message.timestamp}>
          {message.timestamp}
        </time>
      ) : null}
    </div>
  );
}

export default ChatMessage;
