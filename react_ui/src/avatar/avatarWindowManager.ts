/**
 * Avatar Popup Window Manager.
 *
 * Manages the standalone, resizable, movable avatar browser window.
 * Ensures:
 * - Stable window naming to prevent duplicate popups
 * - Automatic focus if already open
 * - Clean URL without JWT tokens
 * - Proper lifecycle detection (closed by user, displaced, etc.)
 */

import { getAvatarSyncBridge } from "./avatarSync";
import type { Companion } from "../api/client";

export const AVATAR_WINDOW_NAME = "ChatbotAvatarWindow";

export interface AvatarWindowOptions {
  companion?: Companion | null;
  sessionId?: string | null;
  roomId?: string | null;
  onOpen?: () => void;
  onClose?: () => void;
}

export class AvatarWindowManager {
  private static instance: AvatarWindowManager | null = null;
  private popupWindow: Window | null = null;
  private checkClosedInterval: ReturnType<typeof setInterval> | null = null;
  private closeCallbacks = new Set<() => void>();
  private openCallbacks = new Set<() => void>();

  public static getInstance(): AvatarWindowManager {
    if (!AvatarWindowManager.instance) {
      AvatarWindowManager.instance = new AvatarWindowManager();
    }
    return AvatarWindowManager.instance;
  }

  private constructor() {
    // Listen for avatar events from the popup window
    const bridge = getAvatarSyncBridge();
    bridge.subscribe((event) => {
      if (event.type === "AVATAR_CLOSED" || event.type === "AVATAR_DISPLACED") {
        this.handleWindowClosed();
      }
    });
  }

  public isOpen(): boolean {
    return this.popupWindow !== null && !this.popupWindow.closed;
  }

  public openAvatarWindow(options?: AvatarWindowOptions): Window | null {
    if (typeof window === "undefined") return null;

    if (options?.onClose) this.closeCallbacks.add(options.onClose);
    if (options?.onOpen) this.openCallbacks.add(options.onOpen);

    // If already open and not closed, focus it and synchronize current state
    if (this.popupWindow && !this.popupWindow.closed) {
      this.popupWindow.focus();
      if (options?.companion && options?.sessionId) {
        this.syncSession(options.companion, options.sessionId, options.roomId);
      }
      return this.popupWindow;
    }

    // Build URL without JWT tokens
    const url = this.buildPopupUrl(options?.companion, options?.sessionId, options?.roomId);

    // Target dimensions suitable for high resolution and secondary monitors
    const width = 920;
    const height = 800;
    const left = window.screenLeft ? window.screenLeft + (window.outerWidth - width) / 2 : 100;
    const top = window.screenTop ? window.screenTop + (window.outerHeight - height) / 2 : 100;

    const windowFeatures = [
      `width=${width}`,
      `height=${height}`,
      `left=${Math.max(0, Math.floor(left))}`,
      `top=${Math.max(0, Math.floor(top))}`,
      "resizable=yes",
      "scrollbars=no",
      "status=no",
      "location=no",
      "toolbar=no",
      "menubar=no",
    ].join(",");

    try {
      this.popupWindow = window.open(url, AVATAR_WINDOW_NAME, windowFeatures);
    } catch (err) {
      console.error("Failed to open avatar window", err);
      return null;
    }

    if (this.popupWindow) {
      this.popupWindow.focus();

      // Start polling interval to detect when user closes the window manually
      if (this.checkClosedInterval) clearInterval(this.checkClosedInterval);
      this.checkClosedInterval = setInterval(() => {
        if (!this.popupWindow || this.popupWindow.closed) {
          this.handleWindowClosed();
        }
      }, 500);

      for (const cb of this.openCallbacks) {
        try {
          cb();
        } catch {}
      }
    }

    return this.popupWindow;
  }

  public focus(): void {
    if (this.popupWindow && !this.popupWindow.closed) {
      this.popupWindow.focus();
    }
  }

  public close(): void {
    if (this.popupWindow && !this.popupWindow.closed) {
      try {
        this.popupWindow.close();
      } catch {}
    }
    this.handleWindowClosed();
  }

  public syncSession(
    companion: Companion,
    sessionId: string,
    roomId?: string | null
  ): void {
    const bridge = getAvatarSyncBridge();
    bridge.publish(
      {
        type: "SESSION_SWITCHED",
        companion,
        sessionId,
        roomId,
      },
      this.popupWindow
    );
  }

  private handleWindowClosed(): void {
    if (this.checkClosedInterval) {
      clearInterval(this.checkClosedInterval);
      this.checkClosedInterval = null;
    }

    this.popupWindow = null;

    for (const cb of this.closeCallbacks) {
      try {
        cb();
      } catch {}
    }
  }

  private buildPopupUrl(
    companion?: Companion | null,
    sessionId?: string | null,
    roomId?: string | null
  ): string {
    const params = new URLSearchParams();
    params.set("view", "avatar");

    if (companion?.companion_id) {
      params.set("companion_id", companion.companion_id);
    }
    if (companion?.name) {
      params.set("companion_name", companion.name);
    }
    if (sessionId) {
      params.set("session_id", sessionId);
    }
    if (roomId) {
      params.set("room_id", roomId);
    }

    return `/?${params.toString()}`;
  }
}
