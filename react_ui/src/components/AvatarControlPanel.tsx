import { AuthImage } from "./AuthImage";
import type { Companion } from "../api/client";
import { avatarColorForCompanion, getInitial } from "../utils/avatar";

interface AvatarControlPanelProps {
  companion: Companion | null;
  sessionId: string | null;
  isAvatarActive: boolean;
  onOpenAvatar: () => void;
}

export function AvatarControlPanel({
  companion,
  sessionId,
  isAvatarActive,
  onOpenAvatar,
}: AvatarControlPanelProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center p-6 text-gray-100">
      <div className="w-full max-w-md rounded-2xl border border-gray-700 bg-gray-800/80 p-8 shadow-2xl backdrop-blur">
        {/* Companion Avatar / Initials */}
        <div className="flex flex-col items-center text-center">
          {companion?.avatar_url ? (
            <AuthImage
              src={companion.avatar_url}
              alt={companion.name}
              className="h-28 w-28 rounded-full object-cover ring-4 ring-indigo-500/30 shadow-lg"
            />
          ) : (
            <div
              className="flex h-28 w-28 items-center justify-center rounded-full text-3xl font-bold text-white ring-4 ring-indigo-500/30 shadow-lg"
              style={{
                backgroundColor: companion
                  ? avatarColorForCompanion(companion)
                  : "#4f46e5",
              }}
            >
              {companion ? getInitial(companion.name) : "?"}
            </div>
          )}

          <h2 className="mt-4 text-2xl font-bold text-gray-100">
            {companion?.name ?? "Companion Avatar"}
          </h2>
          <p className="mt-1 text-sm text-gray-400">
            {companion?.avatar_header || "Real-time Animated Character"}
          </p>
        </div>

        {/* Feature Overview */}
        <div className="mt-6 rounded-xl border border-gray-700/60 bg-gray-900/60 p-4 text-xs text-gray-300 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-emerald-400">●</span>
            <span><strong>AVTR Animation:</strong> Synchronized facial expression & lip-sync</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-indigo-400">●</span>
            <span><strong>Qwen Voice:</strong> High-fidelity real-time conversational speech</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-amber-400">●</span>
            <span><strong>Faster-Whisper:</strong> Low-latency streaming voice recognition</span>
          </div>
        </div>

        {/* Status Indicator */}
        <div className="mt-6 flex items-center justify-between rounded-lg border border-gray-700 bg-gray-900/40 px-4 py-3">
          <span className="text-sm text-gray-400">Avatar Window Status:</span>
          <span
            className={`flex items-center gap-2 text-sm font-medium ${
              isAvatarActive ? "text-emerald-400" : "text-gray-400"
            }`}
          >
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                isAvatarActive ? "bg-emerald-400 animate-ping" : "bg-gray-500"
              }`}
            />
            {isAvatarActive ? "Active & Connected" : "Closed"}
          </span>
        </div>

        {/* Action Button */}
        <div className="mt-6">
          <button
            type="button"
            onClick={onOpenAvatar}
            disabled={!sessionId}
            className={`w-full rounded-xl py-3.5 px-4 text-sm font-semibold transition shadow-lg focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900 ${
              isAvatarActive
                ? "bg-emerald-600 text-white hover:bg-emerald-500 focus:ring-emerald-400"
                : "bg-indigo-600 text-white hover:bg-indigo-500 focus:ring-indigo-400"
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            {isAvatarActive ? "Focus Avatar Window" : "Open Avatar Window"}
          </button>
          {!sessionId && (
            <p className="mt-2 text-center text-xs text-amber-400">
              Select or start a chat session to open the avatar.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default AvatarControlPanel;
