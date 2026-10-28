import { useEffect, useRef, useState } from "react";
import { AvatarRuntime } from "../avatar/avatarRuntime";
import { getAvatarSyncBridge } from "../avatar/avatarSync";
import { getAccessToken, listCompanions, type Companion } from "../api/client";

interface AvatarPopupProps {
  initialCompanionId?: string | null;
  initialSessionId?: string | null;
  initialRoomId?: string | null;
}

export function AvatarPopup({
  initialCompanionId,
  initialSessionId,
  initialRoomId,
}: AvatarPopupProps) {
  const [statusText, setStatusText] = useState("Connecting…");
  const [micEnabled, setMicEnabled] = useState(false);
  const [isDisplaced, setIsDisplaced] = useState(false);
  const [companion, setCompanion] = useState<Companion | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? null);
  const [roomId, setRoomId] = useState<string | null>(initialRoomId ?? null);

  const imgRef = useRef<HTMLImageElement | null>(null);
  const runtimeRef = useRef<AvatarRuntime | null>(null);

  // Sync with main window via AvatarSyncBridge
  useEffect(() => {
    const bridge = getAvatarSyncBridge();

    // Check URL parameters if not passed via props
    const urlParams = new URLSearchParams(window.location.search);
    const companionIdFromUrl = urlParams.get("companion_id") || initialCompanionId;
    const sessionIdFromUrl = urlParams.get("session_id") || initialSessionId;
    const roomIdFromUrl = urlParams.get("room_id") || initialRoomId;

    if (sessionIdFromUrl) setSessionId(sessionIdFromUrl);
    if (roomIdFromUrl) setRoomId(roomIdFromUrl);

    // Fetch initial companion if ID is present
    if (companionIdFromUrl) {
      listCompanions()
        .then((companions) => {
          const match = companions.find((c) => c.companion_id === companionIdFromUrl);
          if (match) setCompanion(match);
        })
        .catch((err) => console.error("Failed to load companion details", err));
    }

    // Subscribe to cross-window sync events
    const unsubscribe = bridge.subscribe((event) => {
      if (event.type === "SESSION_SWITCHED") {
        setCompanion(event.companion);
        setSessionId(event.sessionId);
        setRoomId(event.roomId ?? null);

        if (runtimeRef.current) {
          runtimeRef.current.updateContext({
            companionId: event.companion.companion_id,
            sessionId: event.sessionId,
            roomId: event.roomId,
          });
        }
      }
    });

    // Request latest state from main window in case it opened with pending state
    bridge.publish({ type: "REQUEST_SYNC", tabId: runtimeRef.current?.tabId || "popup" });

    return () => {
      unsubscribe();
    };
  }, [initialCompanionId, initialSessionId, initialRoomId]);

  // Initialize and run the Avatar Runtime
  useEffect(() => {
    const token = getAccessToken() || localStorage.getItem("auth_token");
    const bridge = getAvatarSyncBridge();

    const runtime = new AvatarRuntime(
      {
        onStatusChange: (status) => setStatusText(status),
        onMicStateChange: (enabled) => setMicEnabled(enabled),
        onConnected: () => {
          setIsDisplaced(false);
          bridge.publish({
            type: "AVATAR_CONNECTED",
            companionId: companion?.companion_id,
            sessionId: sessionId || undefined,
            roomId,
            tabId: runtime.tabId,
          });
        },
        onDisconnected: () => {
          bridge.publish({
            type: "AVATAR_DISCONNECTED",
            tabId: runtime.tabId,
          });
        },
        onDisplaced: () => {
          setIsDisplaced(true);
          bridge.publish({
            type: "AVATAR_DISPLACED",
            tabId: runtime.tabId,
          });
        },
        onTranscription: (text, isFinal) => {
          bridge.publish({
            type: "AVATAR_TRANSCRIPTION",
            text,
            isFinal,
            tabId: runtime.tabId,
          });
        },
        onTurnStart: (userMessage, turnId) => {
          if (userMessage) {
            bridge.publish({
              type: "AVATAR_SPOKEN_TURN_START",
              userText: userMessage,
              turnId,
              tabId: runtime.tabId,
            });
          }
        },
        onTurnDelta: (textDelta, turnId) => {
          bridge.publish({
            type: "AVATAR_SPOKEN_TURN_DELTA",
            textDelta,
            turnId,
            tabId: runtime.tabId,
          });
        },
        onTurnEnd: (text, turnId) => {
          bridge.publish({
            type: "AVATAR_SPOKEN_TURN_END",
            assistantText: text || "",
            turnId,
            tabId: runtime.tabId,
          });
        },
      },
      {
        token,
        sessionId,
        companionId: companion?.companion_id,
        roomId,
      }
    );

    runtimeRef.current = runtime;
    runtime.attachImageElement(imgRef.current);
    runtime.start();

    // Auto-enable microphone on window start as per my_avatar default
    runtime.startMic().catch((err) => console.warn("Initial startMic deferred", err));

    const handleBeforeUnload = () => {
      bridge.publish({
        type: "AVATAR_CLOSED",
        tabId: runtime.tabId,
      });
      runtime.stop();
    };

    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      bridge.publish({
        type: "AVATAR_CLOSED",
        tabId: runtime.tabId,
      });
      runtime.stop();
      runtimeRef.current = null;
    };
  }, []);

  const handleToggleMic = async () => {
    if (runtimeRef.current) {
      await runtimeRef.current.toggleMic();
    }
  };

  const handleReconnect = () => {
    setIsDisplaced(false);
    if (runtimeRef.current) {
      runtimeRef.current.stop();
      runtimeRef.current.start();
    }
  };

  const handleClose = () => {
    if (runtimeRef.current) {
      runtimeRef.current.stop();
    }
    window.close();
  };

  // Determine fallback image source: companion avatar_url or default portrait
  const portraitUrl =
    companion?.avatar_url ||
    (companion?.companion_id
      ? `/api/companions/${companion.companion_id}/portrait`
      : "/portrait");

  return (
    <div className="flex h-screen w-screen flex-col items-center justify-between bg-[#11141b] text-[#f5f5f7] select-none p-4 overflow-hidden">
      {/* Top Bar / Companion Info */}
      <header className="flex w-full items-center justify-between border-b border-gray-800/80 px-2 py-1.5 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          <h2 className="text-sm font-semibold truncate text-gray-200">
            {companion?.name ? `${companion.name} — Avatar` : "Animated Avatar"}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleClose}
            className="rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-800 hover:text-white transition"
            title="Close avatar window"
          >
            ✕ Close
          </button>
        </div>
      </header>

      {/* Main Avatar Stage */}
      <main className="relative flex min-h-0 flex-1 w-full items-center justify-center p-2">
        <img
          ref={imgRef}
          id="face"
          src={portraitUrl}
          alt={companion?.name || "Avatar"}
          className="max-h-[82vh] max-w-[min(94vw,1100px)] object-contain rounded-2xl bg-[#1e232d] shadow-2xl transition-all"
        />

        {/* Displacement Overlay (WebSocket close code 4001) */}
        {isDisplaced && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-gray-950/85 backdrop-blur-sm p-6 text-center">
            <div className="max-w-md rounded-xl border border-amber-500/40 bg-gray-900/90 p-6 shadow-2xl">
              <span className="text-3xl">⚠️</span>
              <h3 className="mt-2 text-lg font-bold text-amber-300">
                Window Taken Over
              </h3>
              <p className="mt-2 text-sm text-gray-300">
                Another avatar window has connected to this session. This window has paused to prevent duplicate audio/video streams.
              </p>
              <div className="mt-6 flex justify-center gap-3">
                <button
                  type="button"
                  onClick={handleReconnect}
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition shadow"
                >
                  Reclaim Session
                </button>
                <button
                  type="button"
                  onClick={handleClose}
                  className="rounded-lg bg-gray-800 px-4 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 transition"
                >
                  Close Window
                </button>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Footer Controls matching proven my_avatar UI */}
      <footer className="flex w-full items-center justify-center gap-4 py-2 shrink-0">
        <button
          type="button"
          onClick={handleToggleMic}
          disabled={isDisplaced}
          className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition ${
            micEnabled
              ? "border-emerald-500/50 bg-emerald-950/40 text-emerald-300 hover:bg-emerald-900/50"
              : "border-gray-700 bg-[#293240] text-white hover:bg-[#3b4658]"
          } disabled:opacity-50`}
        >
          <span
            className={`h-2.5 w-2.5 rounded-full ${
              micEnabled ? "bg-emerald-400 animate-ping" : "bg-gray-400"
            }`}
          />
          {micEnabled ? "Disable microphone" : "Enable microphone"}
        </button>

        <span
          id="status"
          className="flex min-w-[150px] items-center justify-center gap-2 rounded-lg bg-gray-900/80 border border-gray-800 px-3 py-1.5 text-sm text-gray-300"
        >
          <span
            id="dot"
            className={`inline-block h-2 w-2 rounded-full ${
              statusText.includes("Listening")
                ? "bg-emerald-400"
                : statusText.includes("Connecting")
                ? "bg-amber-400"
                : statusText.includes("Connected")
                ? "bg-indigo-400"
                : "bg-gray-500"
            }`}
          />
          {statusText}
        </span>

        <button
          type="button"
          onClick={handleClose}
          className="rounded-lg border border-gray-700 bg-[#293240] px-4 py-2 text-sm text-white hover:bg-[#3b4658] transition"
        >
          Exit
        </button>
      </footer>
    </div>
  );
}

export default AvatarPopup;
