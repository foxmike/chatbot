import { useRef, useState } from "react";
import ModelPicker from "./ModelPicker";
import VoiceSelector from "./VoiceSelector";

interface ChatInputProps {
  onSend: (message: string, provider: string, model: string) => void;
  disabled: boolean;
  modelProvider: string;
  modelName: string;
  onModelChange: (provider: string, model: string, sendBlocker: string | null, thinking: boolean) => void;
  /** When set, sending is prevented (e.g. the selected Ollama server is unreachable). */
  sendBlockedReason?: string | null;
  /** The active conversation, whose speaking voice the Voice selector controls. */
  sessionId?: string | null;
}

export function ChatInput({
  onSend,
  disabled,
  modelProvider,
  modelName,
  onModelChange,
  sendBlockedReason = null,
  sessionId = null,
}: ChatInputProps) {
  const [message, setMessage] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustTextareaHeight = () => {
    const textarea = textareaRef.current;

    if (!textarea) {
      return;
    }

    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  };

  const sendMessage = () => {
    const trimmedMessage = message.trim();

    if (!trimmedMessage || disabled || sendBlockedReason) {
      return;
    }

    onSend(trimmedMessage, modelProvider, modelName);
    setMessage("");
    textareaRef.current?.focus();

    requestAnimationFrame(() => {
      const textarea = textareaRef.current;

      if (textarea) {
        textarea.style.height = "auto";
      }
    });
  };

  return (
    <div className="border-t border-gray-700 bg-gray-800 p-4">
      <div className="flex min-w-0 flex-wrap items-start gap-x-3">
        <div className="min-w-0 flex-1">
          <ModelPicker onChange={onModelChange} />
        </div>
        <div className="mb-2">
          <VoiceSelector sessionId={sessionId} />
        </div>
      </div>
      <div className="flex items-end gap-3">
        <textarea
          ref={textareaRef}
          rows={1}
          value={message}
          disabled={disabled}
          autoFocus
          placeholder="Type a message..."
          className="max-h-40 min-h-11 flex-1 resize-none overflow-y-auto rounded-lg border border-gray-600 bg-gray-900 px-4 py-3 text-sm leading-5 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
          onChange={(event) => setMessage(event.target.value)}
          onInput={adjustTextareaHeight}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              sendMessage();
            }
          }}
        />
        <button
          type="button"
          disabled={disabled || Boolean(sendBlockedReason) || message.trim().length === 0}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
          aria-label="Send message"
          title={sendBlockedReason ?? undefined}
          onClick={sendMessage}
        >
          <svg
            className="h-5 w-5"
            viewBox="0 0 20 20"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
          >
            <path
              d="M3.5 10H16.5M16.5 10L11 4.5M16.5 10L11 15.5"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}

export default ChatInput;
