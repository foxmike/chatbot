import { useEffect, useRef, useState } from "react";
import {
  createSession,
  createRoomSession,
  getRoomSession,
  getSessionMessages,
  listCompanions,
  listSessions,
  sendMessage,
} from "./api/client";
import AvatarDisplay from "./components/AvatarDisplay";
import ChatInput from "./components/ChatInput";
import ChatMessage from "./components/ChatMessage";
import MessageList from "./components/MessageList";
import { NotificationFeed } from "./components/NotificationFeed";
import AppShell from "./components/AppShell";
import SettingsPanel from "./components/SettingsPanel";
import { MemoryPanel } from "./components/MemoryPanel";
import TimelinePanel from "./components/TimelinePanel";
import CompanionInfoPanel from "./components/CompanionInfoPanel";
import CompanionSelector from "./components/CompanionSelector";
import { RoomSwitcher } from "./components/RoomSwitcher";
import ScenePanel from "./components/ScenePanel";
import { VoiceControls } from "./components/VoiceControls";
import { CallMode } from "./components/CallMode";
import type { ApiError, CreateSessionResponse, Message, Session } from "./types";
import type { RoomData, RoomType } from "./api/client";
import { AdminDashboard } from "./components/AdminDashboard";
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

function companionInitial(name: string) {
  return name.trim().charAt(0).toUpperCase() || "?";
}

function SwitcherAvatar({ companion }: { companion: Companion }) {
  if (companion.avatar_url) {
    return (
      <img
        src={companion.avatar_url}
        alt={`${companion.name} avatar`}
        className="h-7 w-7 shrink-0 rounded-full object-cover ring-1 ring-gray-600"
      />
    );
  }

  return (
    <span
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ring-1 ring-gray-600"
      style={{ backgroundColor: avatarColorForName(companion.name) }}
    >
      {companionInitial(companion.name)}
    </span>
  );
}

function CompanionSwitcher({
  current,
  onSwitch,
  onManage,
}: {
  current: Companion;
  onSwitch: (companion: Companion) => void;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [companions, setCompanions] = useState<Companion[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    listCompanions().then(setCompanions).catch(() => {});
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        className="flex items-center gap-2 rounded-md px-2 py-1 text-sm font-medium text-gray-200 hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-500/70"
        onClick={() => setOpen((o) => !o)}
      >
        <SwitcherAvatar companion={current} />
        <span className="max-w-[120px] truncate">{current.name}</span>
        <svg className="h-3 w-3 shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-48 rounded-lg border border-gray-700 bg-gray-800 py-1 shadow-xl">
          {companions.length === 0 ? (
            <p className="px-3 py-2 text-xs text-gray-500">Loading…</p>
          ) : (
            companions.map((companion) => {
              const isActive = companion.companion_id === current.companion_id;
              return (
                <button
                  key={companion.companion_id}
                  type="button"
                  className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition hover:bg-gray-700 ${isActive ? "text-indigo-400" : "text-gray-200"}`}
                  onClick={() => {
                    if (!isActive) onSwitch(companion);
                    setOpen(false);
                  }}
                >
                  <SwitcherAvatar companion={companion} />
                  <span className="truncate">{companion.name}</span>
                  {isActive && (
                    <svg className="ml-auto h-3.5 w-3.5 shrink-0 text-indigo-400" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                  )}
                </button>
              );
            })
          )}
          <div className="my-1 border-t border-gray-700" />
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-xs text-gray-500 transition hover:bg-gray-700 hover:text-gray-300"
            onClick={() => { setOpen(false); onManage(); }}
          >
            <svg className="h-3.5 w-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Manage Companions
          </button>
        </div>
      )}
    </div>
  );
}

const ADMIN_PASSWORD = "admin";

function AdminGate() {
  const [password, setPassword] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [error, setError] = useState(false);

  const handleSubmit = () => {
    if (password === ADMIN_PASSWORD) {
      setAuthenticated(true);
      setError(false);
    } else {
      setError(true);
    }
  };

  if (authenticated) {
    return <AdminDashboard />;
  }

  return (
    <section className="flex h-full items-center justify-center p-4 sm:p-6">
      <div className="w-full max-w-sm rounded-lg border border-gray-700 bg-gray-800 p-6">
        <h2 className="text-xl font-semibold text-gray-100">Admin Gate</h2>
        <p className="mt-2 text-sm text-gray-400">
          Enter the admin password to access the dashboard.
        </p>
        <div className="mt-4 flex flex-col gap-3">
          <input
            type="password"
            value={password}
            placeholder="Password"
            className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            onChange={(e) => {
              setPassword(e.target.value);
              setError(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSubmit();
            }}
          />
          <button
            type="button"
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-900"
            onClick={handleSubmit}
          >
            Enter Admin
          </button>
          {error ? (
            <p className="text-sm text-red-300">Incorrect password.</p>
          ) : null}
        </div>
      </div>
    </section>
  );
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeRoomId, setActiveRoomId] = useState<string | null>(null);
  const [activeRoomType, setActiveRoomType] = useState<RoomType | null>(null);
  const [activeRoomTitle, setActiveRoomTitle] = useState<string | null>(null);
  const [messagesBySession, setMessagesBySession] = useState<Record<string, Message[]>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [selectedCompanion, setSelectedCompanion] = useState<Companion | null>(
    null,
  );
  const [callModeActive, setCallModeActive] = useState(false);
  const [useStreamingChat, setUseStreamingChat] = useState(false);
  const [streamRequest, setStreamRequest] = useState<{
    sessionId: string;
    message: string;
  } | null>(null);

  const queueRef = useRef<Array<{ text: string; sessionId: string; roomId: string | null; streaming: boolean }>>([]);
  const processingRef = useRef(false);
  const streamSettleRef = useRef<{ sessionId: string; resolve: () => void; reject: (err: Error) => void } | null>(null);
  const activeRoomIdRef = useRef<string | null>(null);

  const messages = activeSessionId ? messagesBySession[activeSessionId] ?? [] : [];
  const activeSession = sessions.find(
    (session) => session.session_id === activeSessionId,
  );

  useEffect(() => {
    activeRoomIdRef.current = activeRoomId;
  }, [activeRoomId]);

  const upsertSession = (session: Session) => {
    setSessions((currentSessions) => {
      const existingIndex = currentSessions.findIndex(
        (currentSession) => currentSession.session_id === session.session_id,
      );
      if (existingIndex === -1) {
        return [session, ...currentSessions];
      }

      const nextSessions = [...currentSessions];
      nextSessions[existingIndex] = {
        ...nextSessions[existingIndex],
        ...session,
      };
      return nextSessions;
    });
  };

  useEffect(() => {
    if (!user || !selectedCompanion) {
      return;
    }

    let isMounted = true;

    async function initializeSession() {
      try {
        const existingSessions = await listSessions(selectedCompanion?.companion_id);

        if (!isMounted) {
          return;
        }

        if (existingSessions.length > 0) {
          setSessions(existingSessions);
          if (!activeRoomIdRef.current) {
            setActiveSessionId(existingSessions[0].session_id);
          }
          setError(null);
          return;
        }

        const response = await createSession("Chat 1", selectedCompanion?.companion_id);
        const session = buildSession(response, "Chat 1");
        setSessions([session]);
        if (!activeRoomIdRef.current) {
          setActiveSessionId(session.session_id);
        }
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

  const processQueue = async () => {
    if (processingRef.current) return;
    processingRef.current = true;

    while (queueRef.current.length > 0) {
      const item = queueRef.current.shift()!;
      const { text, sessionId, roomId, streaming } = item;

      setIsLoading(true);
      try {
        if (streaming) {
          try {
            await new Promise<void>((resolve, reject) => {
              streamSettleRef.current = { sessionId, resolve, reject };
              setStreamRequest({ sessionId, message: text });
            });
          } catch {
            console.error("Streaming failed for queued message, skipping");
          }
        } else {
          try {
            const response = await sendMessage(text, sessionId, roomId ?? undefined);
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
          }
        }
      } finally {
        setIsLoading(false);
      }
    }

    processingRef.current = false;
  };

  const handleSend = (message: string) => {
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

    queueRef.current.push({
      text: trimmedMessage,
      sessionId,
      roomId: activeRoomId,
      streaming: useStreamingChat && !activeRoomId,
    });

    processQueue();
  };

  const handleStreamComplete = (message: Message) => {
    const pending = streamSettleRef.current;
    streamSettleRef.current = null;

    if (!pending) return;

    setMessagesBySession((currentMessages) => ({
      ...currentMessages,
      [pending.sessionId]: [
        ...(currentMessages[pending.sessionId] ?? []),
        message,
      ],
    }));
    setStreamRequest(null);
    pending.resolve();
  };

  const handleStreamError = (streamError: Error) => {
    const pending = streamSettleRef.current;
    streamSettleRef.current = null;

    if (!pending) return;

    setMessagesBySession((currentMessages) => ({
      ...currentMessages,
      [pending.sessionId]: [
        ...(currentMessages[pending.sessionId] ?? []),
        createMessage("assistant", getErrorDetail(streamError)),
      ],
    }));
    setStreamRequest(null);
    pending.reject(streamError);
  };

  const handleRoomChange = async (
    roomId: string,
    roomType: RoomType,
    room?: RoomData,
  ) => {
    activeRoomIdRef.current = roomId;
    setActiveRoomId(roomId);
    setActiveRoomType(roomType);
    setActiveRoomTitle(room?.display_name ?? null);
    setError(null);

    try {
      const roomSession = await getRoomSession(roomId);
      const session: Session = {
        session_id: roomSession.session_id,
        title: roomSession.title || room?.display_name || "Room Chat",
        created_at: roomSession.created_at,
      };
      upsertSession(session);
      setActiveSessionId(roomSession.session_id);
    } catch (roomSessionError) {
      const apiError = roomSessionError as ApiError;
      if (apiError?.status_code !== 404 || !room) {
        console.error("Failed to load room session", roomSessionError);
        setError(getErrorDetail(roomSessionError));
        return;
      }

      try {
        const response = await createSession(
          room.display_name,
          room.companion_id ?? selectedCompanion?.companion_id,
          roomId,
        );
        await createRoomSession(roomId, response.session_id);
        const session = buildSession(response, room.display_name);
        upsertSession(session);
        setActiveSessionId(session.session_id);
      } catch (createRoomSessionError) {
        console.error("Failed to create room session", createRoomSessionError);
        setError(getErrorDetail(createRoomSessionError));
      }
    }
  };

  const handleRoomCleared = () => {
    activeRoomIdRef.current = null;
    setActiveRoomId(null);
    setActiveRoomType(null);
    setActiveRoomTitle(null);
    setActiveSessionId(null);
    setError(null);
  };

  const handleSwitchCompanion = (companion: Companion) => {
    queueRef.current = [];
    processingRef.current = false;
    streamSettleRef.current = null;
    activeRoomIdRef.current = null;
    setSessions([]);
    setActiveSessionId(null);
    setActiveRoomId(null);
    setActiveRoomType(null);
    setActiveRoomTitle(null);
    setMessagesBySession({});
    setError(null);
    setSelectedCompanion(companion);
  };

  const handleCompanionLogout = () => {
    queueRef.current = [];
    processingRef.current = false;
    streamSettleRef.current = null;
    activeRoomIdRef.current = null;
    setUser(null);
    setSelectedCompanion(null);
    setSessions([]);
    setActiveSessionId(null);
    setActiveRoomId(null);
    setActiveRoomType(null);
    setActiveRoomTitle(null);
    setMessagesBySession({});
    setError(null);
    setIsLoading(false);
    setStreamRequest(null);
    setCallModeActive(false);
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
      <AvatarDisplay companion={selectedCompanion} />
      {error ? (
        <div className="border-b border-red-900/70 bg-red-950/40 p-4">
          <ChatMessage
            message={createMessage("assistant", error)}
            companion={selectedCompanion}
            user={user}
          />
        </div>
      ) : null}

      <MessageList
        messages={messages}
        isLoading={isLoading}
        companion={selectedCompanion}
        user={user}
        streamRequest={streamRequest}
        onStreamComplete={handleStreamComplete}
        onStreamError={handleStreamError}
      />
      <div className="border-t border-gray-700 bg-gray-800 px-4 py-3">
        <label className="flex items-center gap-3 text-sm font-medium text-gray-200">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-gray-600 bg-gray-900 text-indigo-600 focus:ring-2 focus:ring-indigo-500"
            checked={useStreamingChat && !activeRoomId}
            disabled={isLoading || Boolean(activeRoomId)}
            onChange={(event) => setUseStreamingChat(event.target.checked)}
          />
          <span>Stream responses</span>
        </label>
      </div>
      <ChatInput
        onSend={handleSend}
        disabled={!activeSessionId}
      />
    </div>
  );

  return (
    <>
      {callModeActive && activeSessionId ? (
        <CallMode
          sessionId={activeSessionId}
          onEnd={() => setCallModeActive(false)}
        />
      ) : null}
      <AppShell
        user={user}
        companionSwitcher={
          <CompanionSwitcher current={selectedCompanion} onSwitch={handleSwitchCompanion} onManage={() => setSelectedCompanion(null)} />
        }
        activeSessionTitle={
          activeRoomTitle
            ? `${selectedCompanion.name}: ${activeRoomTitle}`
            : activeSession
              ? `${selectedCompanion.name}: ${activeSession.title}`
              : undefined
        }
        companionPanel={<CompanionInfoPanel companion={selectedCompanion} />}
        sidebar={
          <div className="flex h-full w-64 shrink-0 flex-col">
            <div className="min-h-0 flex-1 overflow-hidden">
              <RoomSwitcher
                userId={user.user_id}
                activeRoomId={activeRoomId}
                onRoomChange={handleRoomChange}
                onRoomCleared={handleRoomCleared}
                variant="sidebar"
                autoSelectFirst
                selectCreatedRoom
              />
            </div>
            <div className="shrink-0 border-t border-gray-700 bg-gray-800">
              <NotificationFeed userId={user.user_id} />
            </div>
          </div>
        }
        chatPanel={chatPanel}
        panels={{
        settings: (
          <SettingsPanel
            user={user}
            selectedCompanion={selectedCompanion}
            sessions={sessions}
            activeSessionId={activeSessionId}
            sessionCount={sessions.length}
          />
        ),
        memory: (
          <MemoryPanel sessionId={activeSessionId ?? undefined} />
        ),
        timeline: (
          <TimelinePanel sessionId={activeSessionId ?? ''} />
        ),
        rooms: (
          activeRoomType === "roleplay_scene" ? (
            <ScenePanel roomId={activeRoomId} roomType={activeRoomType} />
          ) : (
            <section className="flex h-full items-center justify-center p-4 sm:p-6">
              <div className="w-full max-w-lg rounded-lg border border-gray-700 bg-gray-800 p-6 text-gray-100">
                <h2 className="text-xl font-semibold">Rooms</h2>
                <p className="mt-2 text-sm text-gray-400">
                  Select a roleplay room from the sidebar to manage its active scene.
                </p>
                {activeRoomTitle ? (
                  <p className="mt-4 text-sm text-gray-300">
                    Active room: {activeRoomTitle}
                  </p>
                ) : null}
              </div>
            </section>
          )
        ),
        voice: (
          <div className="flex h-full flex-col items-center justify-center gap-6 p-4">
            <VoiceControls onTranscription={handleSend} />
            <button
              type="button"
              className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-600 focus:outline-none focus:ring-2 focus:ring-emerald-400 focus:ring-offset-2 focus:ring-offset-gray-900"
              onClick={() => setCallModeActive(true)}
              disabled={!activeSessionId}
            >
              Enter Call Mode
            </button>
          </div>
        ),
        admin: <AdminGate />,
        }}
      />
    </>
  );
}
