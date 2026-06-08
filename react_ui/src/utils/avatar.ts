import type { Companion } from "../api/client";

const AVATAR_COLORS = [
  "#4f46e5",
  "#0891b2",
  "#059669",
  "#7c3aed",
  "#db2777",
  "#ea580c",
  "#ca8a04",
  "#0d9488",
  "#2563eb",
  "#9333ea",
];

export function avatarColorForKey(key: string) {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

export function avatarColorForCompanion(
  companion: Pick<Companion, "companion_id" | "name">,
) {
  return avatarColorForKey(companion.companion_id || companion.name);
}

export function getInitials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .map((part) => part[0]?.toUpperCase())
      .slice(0, 2)
      .join("") || "?"
  );
}

export function getInitial(name: string): string {
  return name.trim().charAt(0).toUpperCase() || "?";
}
