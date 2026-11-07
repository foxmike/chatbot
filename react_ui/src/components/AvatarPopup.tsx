import { useEffect, useRef, useState } from "react";
import { AvatarRuntime } from "../avatar/avatarRuntime";
import { getAvatarSyncBridge } from "../avatar/avatarSync";
import { getAccessToken, listCompanions, type Companion } from "../api/client";
import {
  DEFAULT_TTS_MODE,
  TTS_MODES,
  isTtsMode,
  loadTtsMode,
  saveTtsMode,
  type TtsMode,
} from "../avatar/ttsMode";
import VoiceSelector from "./VoiceSelector";
import { STORAGE_KEY, routingFromStorage, type RoutingSelection } from "../utils/modelPicker";

/** The model picker's persisted selection, read fresh each time it is needed. */
function currentRouting(): RoutingSelection {
  try {
    return routingFromStorage(localStorage.getItem(STORAGE_KEY));
  } catch {
    return routingFromStorage(null);
  }
}

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

  // Restored from localStorage so the choice survives reopening the popup; Natural when unset.
  const [ttsMode, setTtsMode] = useState<TtsMode>(() => {
    try {
      return loadTtsMode(localStorage);
    } catch {
      return DEFAULT_TTS_MODE;
    }
  });
  const ttsModeRef = useRef<TtsMode>(ttsMode);
  ttsModeRef.current = ttsMode;

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
        // The companion fetch has not resolved on first mount; the id is already in the URL.
        companionId:
          companion?.companion_id ??
          initialCompanionId ??
          new URLSearchParams(window.location.search).get("companion_id") ??
          undefined,
        roomId,
        // Spoken turns use the model picker's selection (shared via localStorage).
        getRouting: currentRouting,
        // Sent with the handshake, so the mode is set before the first speech turn.
        getTtsMode: () => ttsModeRef.current,
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

    // The picker lives in the main window; its changes arrive as storage events.
    const handleStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) runtime.sendRouting();
    };
    window.addEventListener("storage", handleStorage);

    return () => {
      window.removeEventListener("storage", handleStorage);
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

  // Speech exists from the moment a response starts ("Responding…") until playback has ended.
  const canSkipSpeech = !isDisplaced && statusText.startsWith("Responding");

  // Applies from the next speech turn; a turn already speaking keeps the mode it started with.
  const handleTtsModeChange = (value: string) => {
    if (!isTtsMode(value)) return;
    setTtsMode(value);
    try {
      saveTtsMode(localStorage, value);
    } catch {
      // Persistence is best-effort.
    }
    ttsModeRef.current = value;
    runtimeRef.current?.sendTtsMode();
  };

  const handleSkipSpeech = () => {
    runtimeRef.current?.skipSpeech();
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
          className="h-full w-full min-h-0 object-contain rounded-2xl bg-[#1e232d] shadow-2xl transition-all"
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
      <footer className="flex w-full flex-wrap items-center justify-center gap-4 py-2 shrink-0">
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

        <label
          htmlFor="tts-mode"
          className="flex items-center gap-2 text-sm font-medium text-gray-300"
          title="How the reply is cut for speech. Applies from the next reply."
        >
          TTS:
          <select
            id="tts-mode"
            aria-label="TTS"
            value={ttsMode}
            onChange={(event) => handleTtsModeChange(event.target.value)}
            className="rounded-lg border border-gray-700 bg-[#293240] px-2 py-2 text-sm text-white transition hover:bg-[#3b4658] focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {TTS_MODES.map((mode) => (
              <option key={mode.id} value={mode.id}>
                {mode.label}
              </option>
            ))}
          </select>
        </label>

        <VoiceSelector sessionId={sessionId} variant="popup" />

        <button
          type="button"
          id="skip-speech"
          onClick={handleSkipSpeech}
          disabled={!canSkipSpeech}
          title="Stop speaking this response; the text keeps streaming"
          className="flex items-center gap-2 rounded-lg border border-amber-500/50 bg-amber-950/40 px-4 py-2 text-sm font-medium text-amber-200 transition hover:bg-amber-900/50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Skip Speech
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
