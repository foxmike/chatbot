/**
 * Avatar Media Runtime.
 *
 * Implements the browser-side audio/video engine from my_avatar:
 * - Web Audio scheduling on native AudioContext
 * - Audio-clock-based video frame synchronization (rAF)
 * - Binary microphone, audio, and video protocol
 * - AVTR frame presentation timestamps (pts)
 * - Playback start/end/drift acknowledgements
 * - Interruption playback fade (15ms time-constant, 60ms hard stop)
 * - Queue clearing and stale turn rejection
 * - 60ms default synchronization buffer
 * - Zero React state updates on video frames for optimal performance
 */

import {
  PACKET_AUDIO,
  PACKET_MIC,
  PACKET_VIDEO,
  WS_CLOSE_DISPLACED,
  encodeMicPacket,
  getAvatarWsUrl,
  parseBinaryPacket,
} from "./avatarProtocol";
import type {
  AvatarClientMessage,
  AvatarServerMessage,
} from "./avatarProtocol";

export interface AvatarRuntimeCallbacks {
  onStatusChange?: (status: string) => void;
  onMicStateChange?: (enabled: boolean) => void;
  onDisplaced?: () => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onTranscription?: (text: string, isFinal: boolean) => void;
  onTurnStart?: (userMessage?: string, turnId?: string) => void;
  onTurnDelta?: (text: string, turnId?: string) => void;
  onTurnEnd?: (text?: string, turnId?: string) => void;
  onError?: (error: string) => void;
}

export interface AudioSegment {
  id: string;
  start: number;
  end: number;
  source: AudioBufferSourceNode;
  gain: GainNode;
  frames: Array<{ pts: number; jpeg: ArrayBuffer }>;
  started: boolean;
  firstDisplayed: boolean;
  interrupted: boolean;
  bufferDelayMs: number;
  gapMs: number;
}

export class AvatarRuntime {
  public tabId: string;
  private ws: WebSocket | null = null;
  private audioContext: AudioContext | null = null;
  private micStream: MediaStream | null = null;
  private micNode: MediaStreamAudioSourceNode | null = null;
  private micProcessor: ScriptProcessorNode | null = null;
  private micSilenceGain: GainNode | null = null;
  private micEnabled = false;

  private playbackEnd = 0;
  private lastFrameUrl: string | null = null;
  private idleFrame: ArrayBuffer | null = null;
  private syncBufferMs = 60; // Proven 60ms default
  private segments = new Map<string, AudioSegment>();
  private pendingVideo = new Map<string, Array<{ pts: number; jpeg: ArrayBuffer }>>();
  private finishedIds = new Set<string>();
  private audioChain: Promise<void> = Promise.resolve();
  private playbackGeneration = 0;
  private lastBufferReport = 0;
  private droppedFrames = 0;
  private lastDisplayedId: string | null = null;
  private lastDisplayedPts = 0;

  private isRunning = false;
  private rafId: number | null = null;
  private targetImageElement: HTMLImageElement | null = null;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private displaced = false;

  // Session & Auth context
  private token: string | null = null;
  private sessionId: string | null = null;
  private companionId: string | null = null;
  private roomId: string | null = null;

  constructor(
    private callbacks: AvatarRuntimeCallbacks = {},
    options?: {
      token?: string | null;
      sessionId?: string | null;
      companionId?: string | null;
      roomId?: string | null;
      syncBufferMs?: number;
    }
  ) {
    this.tabId = Math.random().toString(36).slice(2, 8);
    this.token = options?.token ?? null;
    this.sessionId = options?.sessionId ?? null;
    this.companionId = options?.companionId ?? null;
    this.roomId = options?.roomId ?? null;
    if (typeof options?.syncBufferMs === "number") {
      this.syncBufferMs = options.syncBufferMs;
    }
  }

  public attachImageElement(img: HTMLImageElement | null): void {
    this.targetImageElement = img;
  }

  public updateContext(params: {
    sessionId?: string | null;
    companionId?: string | null;
    roomId?: string | null;
    token?: string | null;
  }): void {
    if (params.sessionId !== undefined) this.sessionId = params.sessionId;
    if (params.companionId !== undefined) this.companionId = params.companionId;
    if (params.roomId !== undefined) this.roomId = params.roomId;
    if (params.token !== undefined) this.token = params.token;

    // If connected, notify backend of session switch
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.sendJson({
        type: "switch_session",
        session_id: this.sessionId,
        companion_id: this.companionId,
        room_id: this.roomId,
      });
    }
  }

  public start(): void {
    if (this.isRunning) return;
    this.isRunning = true;
    this.displaced = false;

    this.connect();
    this.startRenderLoop();
  }

  public stop(): void {
    this.isRunning = false;
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }

    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }

    this.stopMic();
    this.clearSpeech();

    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      this.ws.onopen = null;
      try {
        this.ws.close();
      } catch {}
      this.ws = null;
    }

    if (this.lastFrameUrl) {
      URL.revokeObjectURL(this.lastFrameUrl);
      this.lastFrameUrl = null;
    }

    if (this.audioContext && this.audioContext.state !== "closed") {
      try {
        this.audioContext.close();
      } catch {}
      this.audioContext = null;
    }

    this.callbacks.onDisconnected?.();
  }

  public isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  public isMicActive(): boolean {
    return this.micEnabled;
  }

  public sendJson(message: AvatarClientMessage): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  // --- WebSocket Connection ---

  private connect(): void {
    if (!this.isRunning || this.displaced) return;

    const wsUrl = getAvatarWsUrl(this.tabId);
    this.callbacks.onStatusChange?.("Connecting…");

    try {
      this.ws = new WebSocket(wsUrl);
      this.ws.binaryType = "arraybuffer";
    } catch (err) {
      console.error("Failed to construct WebSocket", err);
      this.callbacks.onStatusChange?.("Connection failed");
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.callbacks.onStatusChange?.("Connected");
      this.callbacks.onConnected?.();

      // Send initial authentication & context handshake
      this.sendJson({
        type: "auth",
        token: this.token,
        session_id: this.sessionId,
        companion_id: this.companionId,
        room_id: this.roomId,
        tab_id: this.tabId,
      });

      // Automatically enable mic if previously requested or as default
      if (this.micEnabled) {
        this.sendJson({ type: "mic", enabled: true });
      }
    };

    this.ws.onerror = () => {
      console.warn("Avatar WebSocket error");
    };

    this.ws.onclose = (event: CloseEvent) => {
      console.warn(
        `Avatar WebSocket closed: code ${event.code}, reason "${event.reason}", clean ${event.wasClean}`
      );
      this.clearSpeech();

      if (event.code === WS_CLOSE_DISPLACED) {
        // Code 4001: Connection takeover - another avatar window took over session.
        // MUST NOT reconnect automatically!
        this.displaced = true;
        this.micEnabled = false;
        this.callbacks.onMicStateChange?.(false);
        this.callbacks.onStatusChange?.("Another window took over - reload to use this one");
        this.callbacks.onDisplaced?.();
        return;
      }

      this.callbacks.onStatusChange?.("Disconnected");
      this.callbacks.onDisconnected?.();
      this.scheduleReconnect();
    };

    this.ws.onmessage = async (event: MessageEvent) => {
      // 1. Text / JSON messages
      if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data) as AvatarServerMessage;
          this.handleServerMessage(msg);
        } catch (err) {
          console.error("Failed to parse JSON WebSocket message", err);
        }
        return;
      }

      // 2. Binary Packets ('V' = 86 video, 'A' = 65 audio)
      if (event.data instanceof ArrayBuffer) {
        this.handleBinaryPacket(event.data);
      }
    };
  }

  private scheduleReconnect(): void {
    if (!this.isRunning || this.displaced) return;
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    this.reconnectTimeout = setTimeout(() => {
      this.connect();
    }, 1500);
  }

  private handleServerMessage(msg: AvatarServerMessage): void {
    switch (msg.type) {
      case "status":
        this.callbacks.onStatusChange?.(msg.value);
        break;
      case "config":
        if (typeof msg.sync_buffer_ms === "number") {
          this.syncBufferMs = msg.sync_buffer_ms;
        }
        break;
      case "interrupt":
        this.clearSpeech();
        break;
      case "transcription":
        this.callbacks.onTranscription?.(msg.text, Boolean(msg.is_final));
        break;
      case "turn_start":
        this.callbacks.onTurnStart?.(msg.user_message, msg.turn_id);
        break;
      case "turn_delta":
      case "text_delta":
      case "token":
        this.callbacks.onTurnDelta?.(msg.text, msg.turn_id);
        break;
      case "turn_end":
      case "turn_complete":
        this.callbacks.onTurnEnd?.(msg.text, msg.turn_id);
        break;
      case "error":
        this.callbacks.onError?.(msg.error);
        break;
    }
  }

  private handleBinaryPacket(data: ArrayBuffer): void {
    const packet = parseBinaryPacket(data);
    if (!packet) return;

    if (packet.type === "video") {
      const { id, pts, jpeg, isIdle } = packet;
      if (isIdle || !id) {
        this.idleFrame = jpeg;
      } else if (!this.finishedIds.has(id)) {
        if (!this.segments.has(id) && !this.pendingVideo.has(id)) {
          this.pendingVideo.set(id, []);
        }
        const frames = this.segments.has(id)
          ? this.segments.get(id)!.frames
          : this.pendingVideo.get(id)!;
        frames.push({ pts, jpeg });
        if (frames.length > 150) {
          frames.shift();
          this.droppedFrames++;
        }
      }
    } else if (packet.type === "audio") {
      const { id, sampleRate, pcm } = packet;
      const generation = this.playbackGeneration;

      this.audioChain = this.audioChain
        .then(async () => {
          if (generation !== this.playbackGeneration || this.finishedIds.has(id)) {
            return;
          }

          if (!this.audioContext) {
            const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
            this.audioContext = new AudioCtx();
          }

          if (this.audioContext.state === "suspended") {
            await this.audioContext.resume();
          }

          if (generation !== this.playbackGeneration) return;

          const buffer = this.audioContext.createBuffer(1, pcm.length, sampleRate);
          buffer.getChannelData(0).set(pcm);

          const source = this.audioContext.createBufferSource();
          const gain = this.audioContext.createGain();
          source.buffer = buffer;
          source.connect(gain);
          gain.connect(this.audioContext.destination);

          const previousEnd = this.playbackEnd;
          const begin = Math.max(
            this.audioContext.currentTime + this.syncBufferMs / 1000,
            previousEnd
          );
          this.playbackEnd = begin + buffer.duration;

          const segment: AudioSegment = {
            id,
            start: begin,
            end: this.playbackEnd,
            source,
            gain,
            frames: this.pendingVideo.get(id) || [],
            started: false,
            firstDisplayed: false,
            interrupted: false,
            bufferDelayMs: (begin - this.audioContext.currentTime) * 1000,
            gapMs: previousEnd > 0 ? Math.max(0, (begin - previousEnd) * 1000) : 0,
          };

          this.pendingVideo.delete(id);
          this.segments.set(id, segment);

          this.sendJson({
            type: "segment_scheduled",
            id,
            ctx_now: this.audioContext.currentTime,
            begin,
            end: this.playbackEnd,
            prev_end: previousEnd,
          });

          source.onended = () => {
            if (!segment.interrupted) {
              this.sendJson({ type: "playback_end", id });
            }
            this.segments.delete(id);
            this.finishedIds.add(id);
            if (this.finishedIds.size > 100) {
              this.finishedIds.clear();
            }
          };

          source.start(begin);
        })
        .catch((error) => console.error("Audio scheduling failed", error));
    }
  }

  // --- Speech Interruption & Queue Clearing ---

  public clearSpeech(): void {
    this.playbackGeneration++;
    const now = this.audioContext?.currentTime || 0;

    for (const segment of this.segments.values()) {
      segment.interrupted = true;
      this.finishedIds.add(segment.id);
      try {
        segment.gain.gain.cancelScheduledValues(now);
        segment.gain.gain.setTargetAtTime(0, now, 0.015);
        segment.source.stop(now + 0.06);
      } catch {}
    }

    for (const id of this.pendingVideo.keys()) {
      this.finishedIds.add(id);
    }

    this.segments.clear();
    this.pendingVideo.clear();
    this.playbackEnd = now;
    this.lastDisplayedId = null;

    this.sendJson({ type: "interruption_ack", fade_ms: 60 });
  }

  // --- Video Render Loop & Audio-Clock Synchronization ---

  private startRenderLoop(): void {
    const render = () => {
      if (!this.isRunning) return;

      if (this.audioContext) {
        const now = this.audioContext.currentTime;
        const current = [...this.segments.values()]
          .sort((a, b) => a.start - b.start)
          .find((s) => !s.interrupted && now >= s.start && now < s.end + 0.04);

        if (current) {
          if (!current.started) {
            current.started = true;
            const outputLatency =
              (this.audioContext.outputLatency ||
                (this.audioContext as unknown as { baseLatency?: number }).baseLatency ||
                0) * 1000;
            this.sendJson({
              type: "playback_start",
              id: current.id,
              buffer_delay_ms: current.bufferDelayMs,
              output_latency_ms: outputLatency,
              gap_ms: current.gapMs,
            });
          }

          const outputLatency =
            this.audioContext.outputLatency ||
            (this.audioContext as unknown as { baseLatency?: number }).baseLatency ||
            0;
          const target = Math.max(0, now - current.start - outputLatency);

          let chosen: { pts: number; jpeg: ArrayBuffer } | null = null;
          while (current.frames.length && current.frames[0].pts <= target + 0.02) {
            chosen = current.frames.shift() || null;
          }

          if (chosen) {
            this.displayFrame(chosen.jpeg, current.id, chosen.pts);
            this.lastDisplayedId = current.id;
            this.lastDisplayedPts = chosen.pts;
          }
        } else if (this.idleFrame) {
          this.displayFrame(this.idleFrame, null, 0);
          this.idleFrame = null;
          this.lastDisplayedId = null;
        }

        // Periodic buffer report (every 1s)
        if (performance.now() - this.lastBufferReport >= 1000) {
          this.lastBufferReport = performance.now();
          const videoFrames = [...this.segments.values()].reduce(
            (n, s) => n + s.frames.length,
            0
          );
          this.sendJson({
            type: "buffer_stats",
            audio_seconds: Math.max(0, this.playbackEnd - now),
            video_frames: videoFrames,
            dropped: this.droppedFrames,
            displayed_id: this.lastDisplayedId,
            displayed_pts: this.lastDisplayedPts,
          });
        }
      }

      this.rafId = requestAnimationFrame(render);
    };

    this.rafId = requestAnimationFrame(render);
  }

  private displayFrame(jpeg: ArrayBuffer, id: string | null, pts: number): void {
    if (!this.targetImageElement) return;

    const blob = new Blob([jpeg], { type: "image/jpeg" });
    const url = URL.createObjectURL(blob);
    const img = this.targetImageElement;

    img.onload = () => {
      if (this.lastFrameUrl) {
        URL.revokeObjectURL(this.lastFrameUrl);
      }
      this.lastFrameUrl = url;

      if (id) {
        const segment = this.segments.get(id);
        if (segment && !segment.firstDisplayed) {
          segment.firstDisplayed = true;
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              if (segment.interrupted || !this.segments.has(id) || !this.audioContext) return;
              const outputLatency =
                this.audioContext.outputLatency ||
                (this.audioContext as unknown as { baseLatency?: number }).baseLatency ||
                0;
              const heard = this.audioContext.currentTime - segment.start - outputLatency;
              this.sendJson({
                type: "video_displayed",
                id,
                pts,
                drift_ms: (heard - pts) * 1000,
              });
            })
          );
        }
      }
    };

    img.src = url;
  }

  // --- Microphone Recording & Resampling ---

  public async toggleMic(): Promise<void> {
    if (this.micEnabled) {
      await this.disableMic();
    } else {
      await this.startMic();
    }
  }

  public async startMic(): Promise<void> {
    if (this.micStream) {
      this.micEnabled = true;
      this.callbacks.onMicStateChange?.(true);
      this.sendJson({ type: "mic", enabled: true });
      this.callbacks.onStatusChange?.("Listening");
      return;
    }

    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      if (!this.audioContext) {
        const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        this.audioContext = new AudioCtx();
      }

      if (this.audioContext.state === "suspended") {
        await this.audioContext.resume();
      }

      this.micNode = this.audioContext.createMediaStreamSource(this.micStream);
      this.micProcessor = this.audioContext.createScriptProcessor(2048, 1, 1);

      let carry = 0;
      let sum = 0;
      let count = 0;
      const inputRate = this.audioContext.sampleRate;

      this.micProcessor.onaudioprocess = (e: AudioProcessingEvent) => {
        if (!this.micEnabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
          return;
        }

        const input = e.inputBuffer.getChannelData(0);
        const output: number[] = [];

        // Resample inputRate to 16000 Hz
        for (let i = 0; i < input.length; i++) {
          sum += input[i];
          count++;
          carry += 16000;
          if (carry >= inputRate) {
            output.push(sum / count);
            carry -= inputRate;
            sum = 0;
            count = 0;
          }
        }

        if (output.length > 0) {
          const pcm = new Float32Array(output);
          const packet = encodeMicPacket(pcm);
          this.ws.send(packet);
        }
      };

      // Create a zero-gain node to route processor output to destination
      // (ScriptProcessor must connect to destination to process audio, but zero gain prevents echo)
      this.micSilenceGain = this.audioContext.createGain();
      this.micSilenceGain.gain.value = 0;

      this.micNode.connect(this.micProcessor);
      this.micProcessor.connect(this.micSilenceGain);
      this.micSilenceGain.connect(this.audioContext.destination);

      this.micEnabled = true;
      this.callbacks.onMicStateChange?.(true);
      this.sendJson({ type: "mic", enabled: true });
      this.callbacks.onStatusChange?.("Listening");
    } catch (err) {
      console.error("Microphone access failed", err);
      this.callbacks.onStatusChange?.("Microphone access needed");
      this.callbacks.onError?.("Microphone permission denied or unavailable");
    }
  }

  public async disableMic(): Promise<void> {
    this.micEnabled = false;
    this.callbacks.onMicStateChange?.(false);
    this.sendJson({ type: "mic", enabled: false });
    this.callbacks.onStatusChange?.("Microphone disabled");
  }

  public stopMic(): void {
    this.micEnabled = false;
    this.callbacks.onMicStateChange?.(false);

    if (this.micProcessor) {
      try {
        this.micProcessor.disconnect();
      } catch {}
      this.micProcessor = null;
    }

    if (this.micSilenceGain) {
      try {
        this.micSilenceGain.disconnect();
      } catch {}
      this.micSilenceGain = null;
    }

    if (this.micNode) {
      try {
        this.micNode.disconnect();
      } catch {}
      this.micNode = null;
    }

    if (this.micStream) {
      for (const track of this.micStream.getTracks()) {
        track.stop();
      }
      this.micStream = null;
    }
  }
}
