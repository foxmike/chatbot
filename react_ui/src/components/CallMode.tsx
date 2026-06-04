import { useCallback, useEffect, useRef, useState } from "react";
import { useAudioRecorder } from "../hooks/useAudioRecorder";
import {
  sendMessage,
  synthesizeSpeech,
  transcribeAudio,
} from "../api/client";
import type { ApiError } from "../types";

export interface CallModeProps {
  sessionId: string;
  onEnd: () => void;
}

interface TranscriptEntry {
  role: "user" | "assistant";
  text: string;
}

function getErrorDetail(error: unknown): string {
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

  return "Something went wrong.";
}

type StatusKind =
  | "idle"
  | "transcribing"
  | "sending"
  | "synthesizing"
  | "playing";

export function CallMode({ sessionId, onEnd }: CallModeProps) {
  const {
    isRecording,
    startRecording,
    stopRecording,
    isSupported,
  } = useAudioRecorder();

  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [status, setStatus] = useState<StatusKind>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const transcriptContainerRef = useRef<HTMLDivElement | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = "";
      }
    };
  }, []);

  // ---- scroll transcript to bottom on new entries ----

  useEffect(() => {
    const container = transcriptContainerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, [transcript]);

  // ---- handle mic toggle ----

  const handleMicToggle = useCallback(async () => {
    setErrorMessage(null);

    if (isRecording) {
      // stop recording -> transcribe -> send message -> synthesize -> play
      try {
        setStatus("transcribing");
        const recorded = await stopRecording();

        if (!isMountedRef.current) return;

        const transcriptionResult = await transcribeAudio({
          audioBase64: recorded.base64,
          format: "webm",
          language: undefined,
        });

        if (!isMountedRef.current) return;

        const userText = transcriptionResult.text;

        setTranscript((prev) => [
          ...prev,
          { role: "user", text: userText },
        ]);

        setStatus("sending");

        const chatResponse = await sendMessage(userText, sessionId);

        if (!isMountedRef.current) return;

        const assistantText = chatResponse.response;

        setTranscript((prev) => [
          ...prev,
          { role: "assistant", text: assistantText },
        ]);

        setStatus("synthesizing");

        const synthResponse = await synthesizeSpeech({
          text: assistantText,
          mood: undefined,
          mode: undefined,
        });

        if (!isMountedRef.current) return;

        setAudioUrl(synthResponse.audioUrl);
        setStatus("playing");

        // audio playback is triggered via useEffect on audioUrl
      } catch (err) {
        if (!isMountedRef.current) return;

        console.error("Call flow failed", err);
        setErrorMessage(getErrorDetail(err));
        setStatus("idle");
      }
    } else {
      // start recording
      try {
        await startRecording();
      } catch {
        // error captured by useAudioRecorder hook
      }
    }
  }, [isRecording, stopRecording, startRecording, sessionId]);

  // ---- auto-play synthesized audio ----

  useEffect(() => {
    if (audioUrl && status === "playing") {
      const audio = new Audio(audioUrl);

      audio.onended = () => {
        if (isMountedRef.current) {
          setStatus("idle");
          setAudioUrl(null);
        }
      };

      audio.onerror = () => {
        if (isMountedRef.current) {
          setErrorMessage("Failed to play audio response.");
          setStatus("idle");
          setAudioUrl(null);
        }
      };

      audioRef.current = audio;
      audio.play().catch(() => {
        if (isMountedRef.current) {
          setErrorMessage("Audio playback was blocked.");
          setStatus("idle");
          setAudioUrl(null);
        }
      });
    }
  }, [audioUrl, status]);

  // ---- status label ----

  const statusLabel = (() => {
    switch (status) {
      case "transcribing":
        return "Transcribing...";
      case "sending":
        return "Sending...";
      case "synthesizing":
        return "Synthesizing voice...";
      case "playing":
        return "Speaking...";
      default:
        return null;
    }
  })();

  // ---- end call ----

  const handleEndCall = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    onEnd();
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-gray-950 text-gray-100">
      {/* -------- header -------- */}
      <div className="flex shrink-0 items-center justify-between border-b border-gray-800 px-6 py-4">
        <div className="flex items-center gap-3">
          {/* pulsing green dot */}
          <span className="relative flex h-3 w-3">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-3 w-3 rounded-full bg-emerald-500" />
          </span>
          <span className="text-sm font-medium text-gray-200">Connected</span>
        </div>

        {statusLabel ? (
          <span className="text-xs text-gray-400">{statusLabel}</span>
        ) : null}
      </div>

      {/* -------- transcript area -------- */}
      <div
        ref={transcriptContainerRef}
        className="flex-1 overflow-y-auto px-6 py-4"
      >
        {transcript.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-gray-600">
            Tap the microphone to start speaking
          </div>
        ) : (
          <div className="mx-auto flex max-w-lg flex-col gap-4">
            {transcript.map((entry, index) => (
              <div
                key={index}
                className={`flex ${
                  entry.role === "user" ? "justify-end" : "justify-start"
                }`}
              >
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                    entry.role === "user"
                      ? "bg-indigo-700 text-white"
                      : "bg-gray-800 text-gray-200"
                  }`}
                >
                  <span className="mb-1 block text-xs font-semibold opacity-60">
                    {entry.role === "user" ? "You" : "Companion"}
                  </span>
                  {entry.text}
                </div>
              </div>
            ))}

            {/* loading indicator */}
            {status !== "idle" && status !== "playing" ? (
              <div className="flex justify-start">
                <div className="flex items-center gap-2 rounded-2xl bg-gray-800 px-4 py-3 text-sm text-gray-400">
                  <span className="h-2 w-2 animate-bounce rounded-full bg-gray-500" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-gray-500 [animation-delay:150ms]" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-gray-500 [animation-delay:300ms]" />
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>

      {/* -------- error display -------- */}
      {errorMessage ? (
        <div className="mx-6 mb-2 rounded-lg border border-red-800 bg-red-950/40 px-4 py-2 text-sm text-red-200">
          {errorMessage}
          <button
            type="button"
            className="ml-2 underline hover:no-underline"
            onClick={() => setErrorMessage(null)}
          >
            Dismiss
          </button>
        </div>
      ) : null}

      {/* -------- bottom controls -------- */}
      <div className="flex shrink-0 items-center justify-center gap-8 border-t border-gray-800 px-6 py-5">
        {/* end call */}
        <button
          type="button"
          className="flex h-12 w-12 items-center justify-center rounded-full bg-red-600 text-white transition hover:bg-red-500 focus:outline-none focus:ring-2 focus:ring-red-400 focus:ring-offset-2 focus:ring-offset-gray-950"
          aria-label="End call"
          onClick={handleEndCall}
        >
          <svg
            className="h-6 w-6"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.08 4.18 2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.72c.127.96.362 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 1 2.81.7A2 2 0 0 1 22 16.92z" />
            <line x1="2" y1="2" x2="22" y2="22" />
          </svg>
        </button>

        {/* microphone button */}
        <button
          type="button"
          disabled={!isSupported || status === "transcribing" || status === "synthesizing" || status === "playing"}
          className={`flex h-16 w-16 items-center justify-center rounded-full border-2 transition focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-950 ${
            isRecording
              ? "border-red-500 bg-red-600 text-white hover:bg-red-500 focus:ring-red-400"
              : "border-gray-600 bg-gray-800 text-gray-300 hover:border-gray-500 hover:bg-gray-700 focus:ring-indigo-400"
          } ${
            !isSupported
              ? "cursor-not-allowed opacity-40"
              : ""
          }`}
          aria-label={
            !isSupported
              ? "Microphone not supported"
              : isRecording
                ? "Stop recording"
                : "Start recording"
          }
          title={
            !isSupported
              ? "Audio recording is not supported in this browser"
              : undefined
          }
          onClick={handleMicToggle}
        >
          <svg
            className="h-7 w-7"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="22" />
            <line x1="8" y1="22" x2="16" y2="22" />
          </svg>
        </button>
      </div>
    </div>
  );
}

export default CallMode;