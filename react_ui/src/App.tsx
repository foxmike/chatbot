import { useEffect, useState } from "react";
import { createSession, sendMessage } from "./api/client";
import ChatInput from "./components/ChatInput";
import ChatMessage from "./components/ChatMessage";
import MessageList from "./components/MessageList";
import SessionSidebar from "./components/SessionSidebar";
import type { ApiError, CreateSessionResponse, Message, Session } from "./types";
import { AuthScreen } from "./components/AuthScreen";
import type { User } from "./api/client";

function buildSession(
  response: CreateSessionResponse,
  title: string,
): Session {
  return {
    session_id: response.session_id,
    title,
    created_at: new Date().toISOString(),
  };
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

function createMessage(role: Message["role"], content: string): Message {
  return {
    role,
    content,
    timestamp: new Date().toISOString(),
  };
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function initializeSession() {
      try {
        const response = await createSession();

        if (!isMounted) {
          return;
        }

        const session = buildSession(response, "Chat 1");
        setSessions([session]);
        setActiveSessionId(session.session_id);
        setError(null);
      } catch (sessionError) {
        console.error("Failed to create initial session", sessionError);

        if (isMounted) {
          setError(getErrorDetail(sessionError));
        }
      }
    }

    initializeSession();

    return () => {
      isMounted = false;
    };
  }, [user]);

  const handleSend = async (message: string) => {
    const trimmedMessage = message.trim();

    if (!trimmedMessage || !activeSessionId) {
      return;
    }

    setError(null);
    setMessages((currentMessages) => [
      ...currentMessages,
      createMessage("user", trimmedMessage),
    ]);
    setIsLoading(true);

    try {
      const response = await sendMessage(trimmedMessage, activeSessionId);
      setMessages((currentMessages) => [
        ...currentMessages,
        createMessage("assistant", response.response),
      ]);
    } catch (messageError) {
      console.error("Failed to send message", messageError);
      setMessages((currentMessages) => [
        ...currentMessages,
        createMessage("assistant", getErrorDetail(messageError)),
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleNewSession = async () => {
    try {
      const response = await createSession();
      const session = buildSession(response, `Chat ${sessions.length + 1}`);

      setSessions((currentSessions) => [...currentSessions, session]);
      setActiveSessionId(session.session_id);
      setMessages([]);
      setError(null);
    } catch (sessionError) {
      console.error("Failed to create session", sessionError);
      setError(getErrorDetail(sessionError));
    }
  };

  const handleSelectSession = (sessionId: string) => {
    setActiveSessionId(sessionId);
    setMessages([]);
    setError(null);
  };

  if (!user) {
    return <AuthScreen onAuthenticated={setUser} />;
  }

  return (
    <div className="flex h-screen bg-gray-900 text-gray-100">
      <SessionSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={handleSelectSession}
        onNewSession={handleNewSession}
      />

      <main className="flex min-w-0 flex-1 flex-col bg-gray-900">
        {error ? (
          <div className="border-b border-red-900/70 bg-red-950/40 p-4">
            <ChatMessage message={createMessage("assistant", error)} />
          </div>
        ) : null}

        <MessageList messages={messages} isLoading={isLoading} />
        <ChatInput
          onSend={handleSend}
          disabled={isLoading || !activeSessionId}
        />
      </main>
    </div>
  );
}
