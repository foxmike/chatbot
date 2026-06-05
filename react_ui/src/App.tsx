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
import AppShell from "./components/AppShell";
import SettingsPanel from "./components/SettingsPanel";
import { MemoryPanel } from "./components/MemoryPanel";
import CompanionSelector from "./components/CompanionSelector";
import type { ApiError, CreateSessionResponse, Message, Session } from "./types";
import { AuthScreen } from "./components/AuthScreen";
import type { Companion, User } from "./api/client";

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

function PanelStatus({
  title,
  status,
  detail,
}: {
  title: string;
  status: string;
  detail: string;
}) {
  return (
    <section className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto max-w-4xl rounded-lg border border-gray-700 bg-gray-800 p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-100">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-gray-400">{detail}</p>
          </div>
          <span className="shrink-0 rounded border border-amber-800 bg-amber-950/50 px-3 py-1 text-sm font-medium text-amber-200">
            {status}
          </span>
        </div>
      </div>
    </section>
  );
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messagesBySession, setMessagesBySession] = useState<Record<string, Message[]>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [selectedCompanion, setSelectedCompanion] = useState<Companion | null>(
    null,
  );
  const messages = activeSessionId ? messagesBySession[activeSessionId] ?? [] : [];
  const activeSession = sessions.find(
    (session) => session.session_id === activeSessionId,
  );

  useEffect(() => {
    if (!user || !selectedCompanion) {
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
  }, [selectedCompanion, user]);

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

  const handleCompanionLogout = () => {
    setUser(null);
    setSelectedCompanion(null);
    setSessions([]);
    setActiveSessionId(null);
    setMessagesBySession({});
    setError(null);
    setIsLoading(false);
  };

  if (!user) {
    return <AuthScreen onAuthenticated={setUser} />;
  }

  if (!selectedCompanion) {
    return (
      <CompanionSelector
        onSelectCompanion={setSelectedCompanion}
        onLogout={handleCompanionLogout}
      />
    );
  }

  const chatPanel = (
    <div className="flex h-full min-h-0 flex-col">
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
    </div>
  );

  return (
    <AppShell
      user={user}
      activeSessionTitle={
        activeSession ? `${selectedCompanion.name}: ${activeSession.title}` : undefined
      }
      sidebar={
        <SessionSidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelect={handleSelectSession}
          onNewSession={handleNewSession}
          onDelete={handleDeleteSession}
        />
      }
      chatPanel={chatPanel}
      panels={{
        settings: (
          <SettingsPanel
            user={user}
            activeSessionId={activeSessionId}
            activeSessionTitle={
              activeSession
                ? `${selectedCompanion.name}: ${activeSession.title}`
                : undefined
            }
            sessionCount={sessions.length}
          />
        ),
        memory: (
          <MemoryPanel sessionId={activeSessionId ?? undefined} />
        ),
        timeline: (
          <PanelStatus
            title="Timeline"
            status="Guarded"
            detail="Timeline navigation is in place, but the continuity routes are not mounted until the backend route and schema checks are reconciled."
          />
        ),
        rooms: (
          <PanelStatus
            title="Rooms"
            status="Guarded"
            detail="Room controls stay inactive until room ownership checks are verified for the authenticated user."
          />
        ),
        voice: (
          <PanelStatus
            title="Voice"
            status="Guarded"
            detail="Voice controls stay inactive until provider configuration and speech routes are verified."
          />
        ),
        admin: (
          <PanelStatus
            title="Admin"
            status="Guarded"
            detail="Admin panels stay inactive until admin endpoints enforce an authenticated admin policy."
          />
        ),
      }}
    />
  );
}
