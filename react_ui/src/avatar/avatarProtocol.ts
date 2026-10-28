/**
 * Avatar WebSocket Protocol Definitions and Binary Packet Parsing.
 *
 * Implements the proven my_avatar binary audio/video protocol:
 * - 'V' (86): Video frame packet (uint16 idLen, UTF-8 id, float32 pts, JPEG bytes)
 * - 'A' (65): Audio frame packet (uint16 idLen, UTF-8 id, uint32 sampleRate, Float32Array PCM)
 * - 'M' (77): Microphone audio packet (Float32Array PCM 16kHz)
 *
 * Plus JSON control messages and adapter definitions for Chatbot backend.
 */

export const PACKET_VIDEO = 86; // 'V'
export const PACKET_AUDIO = 65; // 'A'
export const PACKET_MIC = 77;   // 'M'

export const WS_CLOSE_DISPLACED = 4001; // Another avatar window took over session

export interface VideoFramePacket {
  type: "video";
  id: string;
  pts: number;
  jpeg: ArrayBuffer;
  isIdle: boolean;
}

export interface AudioSegmentPacket {
  type: "audio";
  id: string;
  sampleRate: number;
  pcm: Float32Array;
}

export type AvatarBinaryPacket = VideoFramePacket | AudioSegmentPacket;

// --- Client -> Server JSON Messages ---

export interface AvatarAuthMessage {
  type: "auth";
  token?: string | null;
  session_id?: string | null;
  companion_id?: string | null;
  room_id?: string | null;
  tab_id?: string;
}

export interface AvatarMicControlMessage {
  type: "mic";
  enabled: boolean;
}

export interface AvatarInterruptionAckMessage {
  type: "interruption_ack";
  fade_ms: number;
}

export interface AvatarPlaybackStartMessage {
  type: "playback_start";
  id: string;
  buffer_delay_ms: number;
  output_latency_ms: number;
  gap_ms: number;
}

export interface AvatarPlaybackEndMessage {
  type: "playback_end";
  id: string;
}

export interface AvatarSegmentScheduledMessage {
  type: "segment_scheduled";
  id: string;
  ctx_now: number;
  begin: number;
  end: number;
  prev_end: number;
}

export interface AvatarVideoDisplayedMessage {
  type: "video_displayed";
  id: string;
  pts: number;
  drift_ms: number;
}

export interface AvatarBufferStatsMessage {
  type: "buffer_stats";
  audio_seconds: number;
  video_frames: number;
  dropped: number;
  displayed_id: string | null;
  displayed_pts: number;
}

export interface AvatarSwitchSessionMessage {
  type: "switch_session";
  session_id?: string | null;
  companion_id?: string | null;
  room_id?: string | null;
}

export interface AvatarTextInputMessage {
  type: "text_input";
  text: string;
  session_id?: string | null;
}

export type AvatarClientMessage =
  | AvatarAuthMessage
  | AvatarMicControlMessage
  | AvatarInterruptionAckMessage
  | AvatarPlaybackStartMessage
  | AvatarPlaybackEndMessage
  | AvatarSegmentScheduledMessage
  | AvatarVideoDisplayedMessage
  | AvatarBufferStatsMessage
  | AvatarSwitchSessionMessage
  | AvatarTextInputMessage;

// --- Server -> Client JSON Messages ---

export interface AvatarStatusMessage {
  type: "status";
  value: string;
}

export interface AvatarConfigMessage {
  type: "config";
  sync_buffer_ms?: number;
  [key: string]: unknown;
}

export interface AvatarInterruptMessage {
  type: "interrupt";
}

export interface AvatarReadyMessage {
  type: "ready";
  [key: string]: unknown;
}

export interface AvatarTranscriptionMessage {
  type: "transcription";
  text: string;
  is_final?: boolean;
}

export interface AvatarTurnStartMessage {
  type: "turn_start";
  user_message?: string;
  turn_id?: string;
}

export interface AvatarTurnDeltaMessage {
  type: "turn_delta" | "text_delta" | "token";
  text: string;
  turn_id?: string;
}

export interface AvatarTurnEndMessage {
  type: "turn_end" | "turn_complete";
  text?: string;
  turn_id?: string;
}

export interface AvatarErrorMessage {
  type: "error";
  error: string;
}

export type AvatarServerMessage =
  | AvatarStatusMessage
  | AvatarConfigMessage
  | AvatarInterruptMessage
  | AvatarReadyMessage
  | AvatarTranscriptionMessage
  | AvatarTurnStartMessage
  | AvatarTurnDeltaMessage
  | AvatarTurnEndMessage
  | AvatarErrorMessage;

const textDecoder = new TextDecoder();
const textEncoder = new TextEncoder();

/**
 * Parses an incoming binary packet from the avatar WebSocket.
 */
export function parseBinaryPacket(data: ArrayBuffer): AvatarBinaryPacket | null {
  if (data.byteLength < 3) return null;

  const view = new DataView(data);
  const packetType = view.getUint8(0);
  const idLength = view.getUint16(1, true);

  if (data.byteLength < 3 + idLength) return null;

  const id = textDecoder.decode(new Uint8Array(data, 3, idLength));
  const payloadOffset = 3 + idLength;

  if (packetType === PACKET_VIDEO) {
    if (data.byteLength < payloadOffset + 4) return null;
    const pts = view.getFloat32(payloadOffset, true);
    const jpeg = data.slice(payloadOffset + 4);
    return {
      type: "video",
      id,
      pts,
      jpeg,
      isIdle: id.length === 0,
    };
  }

  if (packetType === PACKET_AUDIO) {
    if (data.byteLength < payloadOffset + 4) return null;
    const sampleRate = view.getUint32(payloadOffset, true);
    // Float32Array PCM samples: remaining bytes must be aligned
    const pcmBytes = data.slice(payloadOffset + 4);
    const pcm = new Float32Array(pcmBytes);
    return {
      type: "audio",
      id,
      sampleRate,
      pcm,
    };
  }

  return null;
}

/**
 * Creates an outgoing binary packet for microphone PCM audio (16kHz).
 * Packet format: byte 0 = 77 ('M'), followed by Float32Array raw bytes.
 */
export function encodeMicPacket(pcm: Float32Array): Uint8Array {
  const packet = new Uint8Array(1 + pcm.byteLength);
  packet[0] = PACKET_MIC;
  packet.set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength), 1);
  return packet;
}

/**
 * Encodes a video packet (used for testing or mock servers).
 */
export function encodeVideoPacket(id: string, pts: number, jpegBytes: Uint8Array): Uint8Array {
  const idBytes = textEncoder.encode(id);
  const totalLength = 1 + 2 + idBytes.byteLength + 4 + jpegBytes.byteLength;
  const buffer = new Uint8Array(totalLength);
  const view = new DataView(buffer.buffer);

  buffer[0] = PACKET_VIDEO;
  view.setUint16(1, idBytes.byteLength, true);
  buffer.set(idBytes, 3);

  const payloadOffset = 3 + idBytes.byteLength;
  view.setFloat32(payloadOffset, pts, true);
  buffer.set(jpegBytes, payloadOffset + 4);

  return buffer;
}

/**
 * Encodes an audio packet (used for testing or mock servers).
 */
export function encodeAudioPacket(id: string, sampleRate: number, pcm: Float32Array): Uint8Array {
  const idBytes = textEncoder.encode(id);
  const totalLength = 1 + 2 + idBytes.byteLength + 4 + pcm.byteLength;
  const buffer = new Uint8Array(totalLength);
  const view = new DataView(buffer.buffer);

  buffer[0] = PACKET_AUDIO;
  view.setUint16(1, idBytes.byteLength, true);
  buffer.set(idBytes, 3);

  const payloadOffset = 3 + idBytes.byteLength;
  view.setUint32(payloadOffset, sampleRate, true);
  buffer.set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength), payloadOffset + 4);

  return buffer;
}

/**
 * Computes the WebSocket URL for the avatar endpoint.
 *
 * Backend runs on port 8000 by default.
 * When frontend is loaded on Vite (e.g. port 5173 or any other port),
 * connect directly to ws://{hostname}:8000/api/avatar/ws.
 * If already on port 8000, connect to ws://{host}/api/avatar/ws.
 */
export function getAvatarWsUrl(tabId?: string): string {
  if (typeof window === "undefined") {
    const base = "ws://127.0.0.1:8000/api/avatar/ws";
    return tabId ? `${base}?tab=${encodeURIComponent(tabId)}` : base;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const hostname = window.location.hostname || "127.0.0.1";
  const port = window.location.port;

  // If port is not 8000 (e.g. Vite dev port 5173), target backend port 8000
  const targetHost = port && port !== "8000" ? `${hostname}:8000` : window.location.host;
  const baseUrl = `${protocol}//${targetHost}/api/avatar/ws`;

  if (tabId) {
    return `${baseUrl}?tab=${encodeURIComponent(tabId)}`;
  }
  return baseUrl;
}
