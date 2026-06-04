import { useEffect, useState } from "react";
import {
  createSession,
  deleteSession,
  getSessionMessages,
  listSessions,
  sendMessage,
} from "./api/client";
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
    title: response.title || title,
    created_at: response.created_at || new Date().toISOString(),
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
  const [messagesBySession, setMessagesBySession] = useState<Record<string, Message[]>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const messages = activeSessionId ? messagesBySession[activeSessionId] ?? [] : [];

  useEffect(() => {
    if (!user) {
      return;
    }

    let isMounted = true;

    async function initializeSession() {
      try {
        const existingSessions = await listSessions();

        if (!isMounted) {
          return;
        }

        if (existingSessions.length > 0) {
          setSessions(existingSessions);
          setActiveSessionId(existingSessions[0].session_id);
          setError(null);
          return;
        }

        const response = await createSession("Chat 1");
        const session = buildSession(response, "Chat 1");
        setSessions([session]);
        setActiveSessionId(session.session_id);
        setMessagesBySession({ [session.session_id]: [] });
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

  useEffect(() => {
    if (!activeSessionId) {
      return;
    }

    let isMounted = true;
    const sessionId = activeSessionId;

    async function loadMessages() {
      try {
        const loadedMessages = await getSessionMessages(sessionId);

        if (isMounted) {
          setMessagesBySession((currentMessages) => ({
            ...currentMessages,
            [sessionId]: loadedMessages,
          }));
          setError(null);
        }
      } catch (messageError) {
        console.error("Failed to load session messages", messageError);

        if (isMounted) {
          setMessagesBySession((currentMessages) => ({
            ...currentMessages,
            [sessionId]: [],
          }));
          setError(getErrorDetail(messageError));
        }
      }
    }

    loadMessages();

    return () => {
      isMounted = false;
    };
  }, [activeSessionId]);

  const handleSend = async (message: string) => {
    const trimmedMessage = message.trim();
    const sessionId = activeSessionId;

    if (!trimmedMessage || !sessionId) {
      return;
    }

    setError(null);
    setMessagesBySession((currentMessages) => ({
      ...currentMessages,
      [sessionId]: [
        ...(currentMessages[sessionId] ?? []),
        createMessage("user", trimmedMessage),
      ],
    }));
    setIsLoading(true);

    try {
      const response = await sendMessage(trimmedMessage, sessionId);
      setMessagesBySession((currentMessages) => ({
        ...currentMessages,
        [sessionId]: [
          ...(currentMessages[sessionId] ?? []),
          createMessage("assistant", response.response),
        ],
      }));
    } catch (messageError) {
      console.error("Failed to send message", messageError);
      setMessagesBySession((currentMessages) => ({
        ...currentMessages,
        [sessionId]: [
          ...(currentMessages[sessionId] ?? []),
          createMessage("assistant", getErrorDetail(messageError)),
        ],
      }));
    } finally {
      setIsLoading(false);
    }
  };

  const handleNewSession = async () => {
    try {
      const title = `Chat ${sessions.length + 1}`;
      const response = await createSession(title);
      const session = buildSession(response, title);

      setSessions((currentSessions) => [...currentSessions, session]);
      setActiveSessionId(session.session_id);
      setMessagesBySession((currentMessages) => ({
        ...currentMessages,
        [session.session_id]: [],
      }));
      setError(null);
    } catch (sessionError) {
      console.error("Failed to create session", sessionError);
      setError(getErrorDetail(sessionError));
    }
  };

  const handleSelectSession = (sessionId: string) => {
    setActiveSessionId(sessionId);
    setError(null);
  };

  const handleDeleteSession = async (session: Session) => {
    const confirmed = window.confirm(
      `Delete chat "${session.title}". Are you sure?`,
    );
    if (!confirmed) {
      return;
    }

    try {
      await deleteSession(session.session_id);

      const remainingSessions = sessions.filter(
        (currentSession) => currentSession.session_id !== session.session_id,
      );
      setSessions(remainingSessions);
      setMessagesBySession((currentMessages) => {
        const nextMessages = { ...currentMessages };
        delete nextMessages[session.session_id];
        return nextMessages;
      });

      if (activeSessionId !== session.session_id) {
        setError(null);
        return;
      }

      if (remainingSessions.length > 0) {
        setActiveSessionId(remainingSessions[0].session_id);
        setError(null);
        return;
      }

      const response = await createSession("Chat 1");
      const nextSession = buildSession(response, "Chat 1");
      setSessions([nextSession]);
      setActiveSessionId(nextSession.session_id);
      setMessagesBySession((currentMessages) => ({
        ...currentMessages,
        [nextSession.session_id]: [],
      }));
      setError(null);
    } catch (deleteError) {
      console.error("Failed to delete session", deleteError);
      setError(getErrorDetail(deleteError));
    }
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
        onDelete={handleDeleteSession}
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
