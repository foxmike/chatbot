import { useCallback, useEffect, useRef, useState } from "react";

export interface RecordedAudio {
  blob: Blob;
  base64: string;
}

export interface UseAudioRecorderResult {
  isRecording: boolean;
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<RecordedAudio>;
  audioBlob: Blob | null;
  audioBase64: string | null;
  error: string | null;
  isSupported: boolean;
}

function getRecorderOptions(): MediaRecorderOptions | undefined {
  const mimeType = "audio/webm";

  if (
    typeof MediaRecorder.isTypeSupported === "function" &&
    MediaRecorder.isTypeSupported(mimeType)
  ) {
    return { mimeType };
  }

  return undefined;
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onloadend = () => {
      if (typeof reader.result !== "string") {
        reject(new Error("Failed to read recorded audio."));
        return;
      }

      const base64 = reader.result.split(",", 2)[1] ?? "";
      resolve(base64);
    };
    reader.onerror = () => {
      reject(new Error("Failed to read recorded audio."));
    };

    reader.readAsDataURL(blob);
  });
}

function stopStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => {
    track.stop();
  });
}

function getPermissionErrorMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (
      error.name === "NotAllowedError" ||
      error.name === "PermissionDeniedError"
    ) {
      return "Microphone permission was denied.";
    }

    if (error.name === "NotFoundError") {
      return "No microphone was found.";
    }
  }

  return "Unable to start audio recording.";
}

export function useAudioRecorder(): UseAudioRecorderResult {
  const [isRecording, setIsRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioBase64, setAudioBase64] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const isSupported =
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== "undefined";

  const startRecording = useCallback(async () => {
    if (!isSupported) {
      setError("Audio recording is not supported in this browser.");
      return;
    }

    setError(null);
    setAudioBlob(null);
    setAudioBase64(null);
    chunksRef.current = [];

    try {
      stopStream(streamRef.current);
      streamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });

      const recorder = new MediaRecorder(streamRef.current, getRecorderOptions());
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };
      recorder.onstop = () => {
        setIsRecording(false);
      };

      recorder.start();
      setIsRecording(true);
    } catch (recordingError) {
      stopStream(streamRef.current);
      streamRef.current = null;
      mediaRecorderRef.current = null;
      setIsRecording(false);
      setError(getPermissionErrorMessage(recordingError));
    }
  }, [isSupported]);

  const stopRecording = useCallback((): Promise<RecordedAudio> => {
    return new Promise((resolve, reject) => {
      const recorder = mediaRecorderRef.current;

      if (!recorder || recorder.state === "inactive") {
        const message = "No active recording to stop.";
        setError(message);
        reject(new Error(message));
        return;
      }

      recorder.onstop = () => {
        setIsRecording(false);

        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });

        stopStream(streamRef.current);
        streamRef.current = null;
        mediaRecorderRef.current = null;

        blobToBase64(blob)
          .then((base64) => {
            setAudioBlob(blob);
            setAudioBase64(base64);
            resolve({ blob, base64 });
          })
          .catch((readError: unknown) => {
            const message =
              readError instanceof Error
                ? readError.message
                : "Failed to read recorded audio.";
            setError(message);
            reject(readError);
          });
      };
      recorder.onerror = (event) => {
        setIsRecording(false);
        setError(event.error.message);
        reject(event.error);
      };

      recorder.stop();
    });
  }, []);

  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.stop();
      }
      stopStream(streamRef.current);
      streamRef.current = null;
      mediaRecorderRef.current = null;
    };
  }, []);

  return {
    isRecording,
    startRecording,
    stopRecording,
    audioBlob,
    audioBase64,
    error,
    isSupported,
  };
}
