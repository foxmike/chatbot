import { AuthImage } from "./AuthImage";
import type { Companion } from "../api/client";
import { avatarColorForCompanion, getInitials } from "../utils/avatar";

interface AvatarDisplayProps {
  companion: Companion;
  onOpenAvatar?: () => void;
  isAvatarActive?: boolean;
}

export function AvatarDisplay({
  companion,
  onOpenAvatar,
  isAvatarActive = false,
}: AvatarDisplayProps) {
  return (
    <div className="flex items-center justify-between border-b border-gray-700 bg-gray-800/60 py-3 pl-4 pr-24">
      <div className="flex items-center gap-3 min-w-0">
        {companion.avatar_url ? (
          <AuthImage
            src={companion.avatar_url}
            alt={`${companion.name} avatar`}
            className="h-10 w-10 shrink-0 rounded-full object-cover ring-2 ring-gray-600"
          />
        ) : (
          <div
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white ring-2 ring-indigo-500/50"
            style={{ backgroundColor: avatarColorForCompanion(companion) }}
          >
            {getInitials(companion.name)}
          </div>
        )}
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-gray-100">
            {companion.name}
          </p>
          <p className="text-xs text-gray-400">
            {isAvatarActive ? (
              <span className="flex items-center gap-1.5 text-emerald-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Avatar connected
              </span>
            ) : (
              "Text chat"
            )}
          </p>
        </div>
      </div>

      {onOpenAvatar && (
        <button
          type="button"
          onClick={onOpenAvatar}
          className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium shadow-sm transition focus:outline-none focus:ring-2 focus:ring-indigo-500/70 ${
            isAvatarActive
              ? "border-emerald-500/50 bg-emerald-950/40 text-emerald-300 hover:bg-emerald-900/50"
              : "border-indigo-500/40 bg-indigo-600/20 text-indigo-200 hover:bg-indigo-600/30"
          }`}
          title={isAvatarActive ? "Avatar window is active" : "Open animated avatar window"}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              isAvatarActive ? "bg-emerald-400 animate-ping" : "bg-indigo-400"
            }`}
          />
          {isAvatarActive ? "Focus Avatar" : "Open Avatar"}
        </button>
      )}
    </div>
  );
}

export default AvatarDisplay;
