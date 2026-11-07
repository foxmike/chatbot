/**
 * Inter-window communication and synchronization bridge between the main Chatbot window
 * and the Avatar Popup window.
 *
 * Uses BroadcastChannel as primary channel with window.postMessage validation fallback.
 */

import type { Companion } from "../api/client";

export const AVATAR_SYNC_CHANNEL = "chatbot_avatar_sync";

export type AvatarSyncEvent =
  | {
      type: "AVATAR_CONNECTED";
      companionId?: string;
      sessionId?: string;
      roomId?: string | null;
      tabId: string;
    }
  | {
      type: "AVATAR_DISCONNECTED";
      tabId: string;
    }
  | {
      type: "AVATAR_CLOSED";
      tabId: string;
    }
  | {
      type: "AVATAR_DISPLACED"; // WebSocket code 4001 takeover
      tabId: string;
    }
  | {
      type: "AVATAR_TRANSCRIPTION";
      text: string;
      isFinal: boolean;
      tabId: string;
    }
  | {
      type: "SESSION_SWITCHED";
      companion: Companion;
      sessionId: string;
      roomId?: string | null;
    }
  | {
      type: "AVATAR_SPOKEN_TURN_START";
      userText: string;
      turnId?: string;
      tabId: string;
    }
  | {
      type: "AVATAR_SPOKEN_TURN_DELTA";
      textDelta: string;
      turnId?: string;
      tabId: string;
    }
  | {
      type: "AVATAR_SPOKEN_TURN_END";
      userText?: string;
      assistantText: string;
      turnId?: string;
      tabId: string;
    }
  | {
      type: "TYPED_MESSAGE_STREAMING_START";
      userText: string;
      sessionId: string;
    }
  | {
      type: "TYPED_MESSAGE_STREAMING_DELTA";
      delta: string;
      sessionId: string;
    }
  | {
      type: "TYPED_MESSAGE_STREAMING_END";
      fullText: string;
      sessionId: string;
    }
  | {
      // Who speaks in a conversation changed (chat or popup selector); re-read it.
      type: "VOICE_SELECTION_CHANGED";
      sessionId: string;
      voiceId: string | null;
    }
  | {
      // Voices were created, renamed or deleted; selectors refresh their lists.
      type: "VOICE_LIBRARY_CHANGED";
    }
  | {
      type: "REQUEST_SYNC";
      tabId: string;
    }
  | {
      type: "SYNC_RESPONSE";
      companion: Companion | null;
      sessionId: string | null;
      roomId: string | null;
    };

export type AvatarSyncListener = (event: AvatarSyncEvent) => void;

export class AvatarSyncBridge {
  private channel: BroadcastChannel | null = null;
  private listeners = new Set<AvatarSyncListener>();
  private postMessageListener: ((event: MessageEvent) => void) | null = null;
  private readonly targetOrigin: string;

  constructor() {
    this.targetOrigin = typeof window !== "undefined" ? window.location.origin : "";

    if (typeof BroadcastChannel !== "undefined") {
      try {
        this.channel = new BroadcastChannel(AVATAR_SYNC_CHANNEL);
        this.channel.onmessage = (event: MessageEvent) => {
          this.notifyListeners(event.data);
        };
      } catch (err) {
        console.warn("BroadcastChannel not supported or failed to initialize", err);
      }
    }

    if (typeof window !== "undefined") {
      this.postMessageListener = (event: MessageEvent) => {
        // Enforce strict origin validation
        if (event.origin !== this.targetOrigin) return;
        if (event.data && typeof event.data === "object" && "type" in event.data) {
          this.notifyListeners(event.data as AvatarSyncEvent);
        }
      };
      window.addEventListener("message", this.postMessageListener);
    }
  }

  public subscribe(listener: AvatarSyncListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  public publish(event: AvatarSyncEvent, targetWindow?: Window | null): void {
    // 1. BroadcastChannel (broadcasts to all other tabs/windows of same origin)
    if (this.channel) {
      try {
        this.channel.postMessage(event);
      } catch (err) {
        console.error("Failed to post message to BroadcastChannel", err);
      }
    }

    // 2. Direct postMessage fallback (for specific window opener / popup)
    if (targetWindow && !targetWindow.closed && typeof window !== "undefined") {
      try {
        targetWindow.postMessage(event, this.targetOrigin);
      } catch (err) {
        console.error("Failed to post message to target window", err);
      }
    }
  }

  private notifyListeners(data: unknown): void {
    if (!data || typeof data !== "object") return;
    const evt = data as AvatarSyncEvent;
    for (const listener of this.listeners) {
      try {
        listener(evt);
      } catch (err) {
        console.error("Error in AvatarSyncListener", err);
      }
    }
  }

  public destroy(): void {
    if (this.channel) {
      this.channel.close();
      this.channel = null;
    }
    if (this.postMessageListener && typeof window !== "undefined") {
      window.removeEventListener("message", this.postMessageListener);
      this.postMessageListener = null;
    }
    this.listeners.clear();
  }
}

// Global singleton instance for easy cross-component use
let globalBridge: AvatarSyncBridge | null = null;

export function getAvatarSyncBridge(): AvatarSyncBridge {
  if (!globalBridge) {
    globalBridge = new AvatarSyncBridge();
  }
  return globalBridge;
}
