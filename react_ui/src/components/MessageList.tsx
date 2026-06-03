import { useEffect, useRef } from "react";
import ChatMessage from "./ChatMessage";
import type { Message } from "../types";

interface MessageListProps {
  messages: Message[];
  isLoading: boolean;
}

export function MessageList({ messages, isLoading }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;

    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, [messages, isLoading]);

  return (
    <div ref={containerRef} className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
      {messages.length === 0 && !isLoading ? (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
          Start a conversation
        </div>
      ) : null}

      {messages.map((message, index) => (
        <ChatMessage key={`${message.role}-${index}`} message={message} />
      ))}

      {isLoading ? (
        <div className="flex w-full flex-col items-start">
          <div
            className="flex max-w-[80%] items-center gap-1 rounded-lg bg-gray-700 px-4 py-3"
            aria-label="Assistant is typing"
          >
            <span className="h-2 w-2 animate-bounce rounded-full bg-gray-300" />
            <span className="h-2 w-2 animate-bounce rounded-full bg-gray-300 [animation-delay:150ms]" />
            <span className="h-2 w-2 animate-bounce rounded-full bg-gray-300 [animation-delay:300ms]" />
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default MessageList;
