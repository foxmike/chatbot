import { useEffect, useRef, useState } from "react";
import { streamChat } from "../api/client";
import type { StreamCallbacks } from "../api/client";
import ChatMessage from "./ChatMessage";
import type { Message } from "../types";
import type { Companion, User } from "../api/client";

interface MessageListProps {
  messages: Message[];
  isLoading: boolean;
  companion?: Companion | null;
  user?: User | null;
  /**
   * When set to a non-null value, triggers a streaming chat request.
   * Set to null after the stream completes to allow future requests
   * with the same session/message pair.
   */
  streamRequest?: { sessionId: string; message: string } | null;
  /** Called when a streamed assistant message is fully received. */
  onStreamComplete?: (message: Message) => void;
  /** Called when a streaming request encounters an error. */
  onStreamError?: (error: Error) => void;
}

export function MessageList({
  messages,
  isLoading,
  companion,
  user,
  streamRequest,
  onStreamComplete,
  onStreamError,
}: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Stable refs for callbacks so the stream effect doesn't re-fire
  // when the parent passes new inline functions on every render.
  const onStreamCompleteRef = useRef(onStreamComplete);
  onStreamCompleteRef.current = onStreamComplete;
  const onStreamErrorRef = useRef(onStreamError);
  onStreamErrorRef.current = onStreamError;

  // ---- streaming state ---------------------------------------------------
  const [streamingMessage, setStreamingMessage] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isTyping, setIsTyping] = useState(false);

  // ---- abort on unmount --------------------------------------------------
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // ---- streaming lifecycle -----------------------------------------------
  useEffect(() => {
    if (!streamRequest) return;

    const { sessionId, message } = streamRequest;

    // Abort any in-flight stream from a previous request.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    // Reset streaming state for the new request.
    setStreamingMessage(null);
    setIsStreaming(false);
    setIsTyping(false);

    const callbacks: StreamCallbacks = {
      onTyping(status) {
        setIsTyping(status === "started");
      },
      onToken(token) {
        setIsTyping(false);
        setIsStreaming(true);
        setStreamingMessage((prev) => (prev ?? "") + token);
      },
      onResponse(text) {
        setIsStreaming(false);
        setStreamingMessage(null);
        setIsTyping(false);
        onStreamCompleteRef.current?.({
          role: "assistant",
          content: text,
          timestamp: new Date().toISOString(),
        });
      },
      onError(error) {
        setIsStreaming(false);
        setStreamingMessage(null);
        setIsTyping(false);
        onStreamErrorRef.current?.(error);
      },
      onDone() {
        setIsStreaming(false);
        setStreamingMessage(null);
        setIsTyping(false);
      },
    };

    streamChat(sessionId, message, callbacks, controller.signal);

    return () => {
      controller.abort();
    };
  }, [streamRequest]);

  // ---- auto-scroll -------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, [messages, isLoading, streamingMessage, isTyping, isStreaming]);

  // ---- derived render flags ----------------------------------------------
  const showStreamingBubble = isStreaming && streamingMessage !== null;
  const showStreamTypingIndicator = isTyping && streamingMessage === null;
  // Only show the legacy typing indicator when we are NOT in a streaming
  // flow (the old non-streaming path controlled by the isLoading prop).
  const showLegacyTyping = isLoading && !isStreaming && !isTyping;

  // ---- render ------------------------------------------------------------
  return (
    <div
      ref={containerRef}
      className="flex flex-1 flex-col gap-4 overflow-y-auto p-4"
    >
      {messages.length === 0 &&
      !isLoading &&
      !isStreaming &&
      !isTyping ? (
        <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
          Start a conversation
        </div>
      ) : null}

      {messages.map((message, index) => (
        <ChatMessage
          key={`${message.role}-${index}`}
          message={message}
          companion={companion}
          user={user}
        />
      ))}

      {/* Streaming partial-message bubble */}
      {showStreamingBubble ? (
        <ChatMessage
          message={{
            role: "assistant",
            content: streamingMessage,
          }}
          companion={companion}
          user={user}
        />
      ) : null}

      {/* Streaming typing indicator (three animated dots) */}
      {showStreamTypingIndicator ? (
        <ChatMessage
          message={{
            role: "assistant",
            content: "...",
          }}
          companion={companion}
          user={user}
        />
      ) : null}

      {/* Legacy typing indicator (non-streaming, isLoading-based) */}
      {showLegacyTyping ? (
        <ChatMessage
          message={{
            role: "assistant",
            content: "...",
          }}
          companion={companion}
          user={user}
        />
      ) : null}
    </div>
  );
}

export default MessageList;
