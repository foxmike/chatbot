import type { Companion } from "../api/client";
import { avatarColorForCompanion, getInitials } from "../utils/avatar";

interface AvatarDisplayProps {
  companion: Companion;
}

export function AvatarDisplay({ companion }: AvatarDisplayProps) {
  return (
    <div className="flex items-center gap-3 border-b border-gray-700 bg-gray-800/60 px-4 py-3">
      {companion.avatar_url ? (
        <img
          src={companion.avatar_url}
          alt={`${companion.name} avatar`}
          className="h-10 w-10 rounded-full object-cover ring-2 ring-gray-600"
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
      </div>
    </div>
  );
}

export default AvatarDisplay;
