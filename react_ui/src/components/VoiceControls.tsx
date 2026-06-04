import { useCallback, useEffect, useRef, useState } from "react";
import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { transcribeAudio } from "../api/client";
import type { ApiError } from "../types";

export interface VoiceControlsProps {
  onTranscription: (text: string) => void;
  className?: string;
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

  return "Something went wrong. Please try again.";
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(minutes)}:${pad(seconds)}`;
}

export function VoiceControls({ onTranscription, className = "" }: VoiceControlsProps) {
  const {
    isRecording,
    startRecording,
    stopRecording,
    error: recorderError,
    isSupported,
  } = useAudioRecorder();

  const [durationMs, setDurationMs] = useState(0);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcribeError, setTranscribeError] = useState<string | null>(null);
  const recordingStartRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- duration timer ----

  useEffect(() => {
    if (isRecording) {
      recordingStartRef.current = Date.now();
      setDurationMs(0);
      timerRef.current = setInterval(() => {
        if (recordingStartRef.current !== null) {
          setDurationMs(Date.now() - recordingStartRef.current);
        }
      }, 200);
    } else {
      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      recordingStartRef.current = null;
    }

    return () => {
      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [isRecording]);

  // ---- toggle ----

  const handleToggle = useCallback(async () => {
    setTranscribeError(null);

    if (isRecording) {
      try {
        const recorded = await stopRecording();
        setIsTranscribing(true);

        const result = await transcribeAudio({
          audioBase64: recorded.base64,
          format: "webm",
          language: undefined,
        });

        onTranscription(result.text);
      } catch (err) {
        console.error("Transcription failed", err);
        setTranscribeError(getErrorDetail(err));
      } finally {
        setIsTranscribing(false);
        setDurationMs(0);
      }
    } else {
      await startRecording();
    }
  }, [isRecording, startRecording, stopRecording, onTranscription]);

  // ---- unsupported browser ----

  if (!isSupported) {
    return (
      <div className={`relative inline-flex ${className}`}>
        <button
          type="button"
          disabled
          title="Audio recording is not supported in this browser"
          className="flex h-10 w-10 items-center justify-center rounded-full border border-gray-600 bg-gray-800 text-gray-500 transition focus:outline-none"
          aria-label="Microphone not supported"
        >
          <svg
            className="h-5 w-5"
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
            <line x1="5" y1="22" x2="19" y2="22" />
          </svg>
        </button>
        <span className="absolute -top-1 left-1/2 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded bg-gray-950 px-2 py-1 text-xs text-gray-300 opacity-0 transition-opacity group-hover:opacity-100">
          Not supported
        </span>
      </div>
    );
  }

  // ---- busy transcribing ----

  if (isTranscribing) {
    return (
      <div className={`inline-flex items-center gap-3 ${className}`}>
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-indigo-900/60">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-400 border-t-transparent" />
        </div>
        <span className="text-xs text-gray-400">Transcribing...</span>

        {transcribeError ? (
          <span className="rounded border border-red-800 bg-red-950/40 px-2 py-0.5 text-xs text-red-200">
            {transcribeError}
          </span>
        ) : null}
      </div>
    );
  }

  // ---- display error ----

  const displayError = recorderError ?? transcribeError;

  return (
    <div className={`inline-flex flex-col gap-2 ${className}`}>
      <div className="flex items-center gap-3">
        {/* microphone toggle button */}
        <button
          type="button"
          className={`flex h-10 w-10 items-center justify-center rounded-full border transition focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-800 ${
            isRecording
              ? "border-red-700 bg-red-600 text-white hover:bg-red-500 focus:ring-red-400"
              : "border-gray-600 bg-gray-700 text-gray-300 hover:bg-gray-600 focus:ring-indigo-400"
          }`}
          aria-label={isRecording ? "Stop recording" : "Start recording"}
          onClick={handleToggle}
        >
          <svg
            className="h-5 w-5"
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
          </svg>
        </button>

        {/* recording state */}
        {isRecording ? (
          <div className="flex items-center gap-2">
            <span className="flex h-3 w-3">
              <span className="absolute inline-flex h-3 w-3 animate-ping rounded-full bg-red-400 opacity-75" />
              <span className="relative inline-flex h-3 w-3 rounded-full bg-red-500" />
            </span>
            <span className="font-mono text-sm text-red-400">
              {formatDuration(durationMs)}
            </span>
          </div>
        ) : null}

        {displayError && !isTranscribing ? (
          <span className="rounded border border-red-800 bg-red-950/40 px-2 py-0.5 text-xs text-red-200">
            {displayError}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export default VoiceControls;