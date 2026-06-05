import type { Companion } from "../api/client";

interface AvatarDisplayProps {
  companion: Companion;
}

function getInitials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase())
    .slice(0, 2)
    .join("");
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
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-indigo-700 text-sm font-semibold text-white ring-2 ring-indigo-500/50">
          {getInitials(companion.name)}
        </div>
      )}
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-gray-100">
          {companion.name}
        </p>
        {companion.persona ? (
          <p className="truncate text-xs text-gray-400">{companion.persona}</p>
        ) : null}
      </div>
    </div>
  );
}

export default AvatarDisplay;