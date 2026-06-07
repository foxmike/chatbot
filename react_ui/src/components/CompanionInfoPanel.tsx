import type { Companion } from "../api/client";

const PERSONALITY_DIMS = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
  "curiosity",
  "creativity",
] as const;

const DEFAULT_SLIDERS: Record<string, number> = {
  openness: 0.7,
  conscientiousness: 0.6,
  extraversion: 0.5,
  agreeableness: 0.8,
  neuroticism: 0.3,
  curiosity: 0.7,
  creativity: 0.6,
};

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

function getInitials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase())
    .slice(0, 2)
    .join("");
}

function avatarColorForName(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function fmtLabel(v: string) {
  return v
    .replace(/_/g, " ")
    .split(" ")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

interface CompanionInfoPanelProps {
  companion: Companion | null;
}

export function CompanionInfoPanel({ companion }: CompanionInfoPanelProps) {
  if (!companion) {
    return (
      <section className="flex h-full flex-col items-center justify-center bg-gray-900 p-8 text-center text-gray-100">
        <div className="flex max-w-sm flex-col items-center gap-3">
          <div className="rounded-full border border-gray-700 bg-gray-800/60 p-4">
            <svg
              className="h-8 w-8 text-gray-500"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
              />
            </svg>
          </div>
          <h3 className="text-lg font-semibold text-gray-200">
            No Companion Selected
          </h3>
          <p className="text-sm text-gray-500">
            Select a companion from the switcher or list to view their profile details here.
          </p>
        </div>
      </section>
    );
  }

  const sliders = companion.personality_sliders
    ? { ...DEFAULT_SLIDERS, ...companion.personality_sliders }
    : { ...DEFAULT_SLIDERS };

  const bgInitialsColor = avatarColorForName(companion.name);

  return (
    <section className="flex h-full flex-col bg-gray-900 text-gray-100">
      {/* Header */}
      <div className="border-b border-gray-800 bg-gray-950/20 px-6 py-4">
        <h2 className="text-lg font-semibold text-gray-100">Companion Profile</h2>
        <p className="text-xs text-gray-400">
          Overview of your companion's identity, personality, and backstory.
        </p>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="flex flex-col gap-6 max-w-4xl">
          {/* Avatar and Basic Info */}
          <div className="flex flex-col items-center gap-5 rounded-xl border border-gray-800 bg-gray-800/30 p-6 sm:flex-row sm:items-start text-center sm:text-left transition duration-300 hover:border-gray-700/80">
            {companion.avatar_url ? (
              <img
                src={companion.avatar_url}
                alt={`${companion.name} avatar`}
                className="h-24 w-24 shrink-0 rounded-full object-cover ring-4 ring-indigo-500/20 shadow-lg shadow-indigo-950/50"
              />
            ) : (
              <div
                className="flex h-24 w-24 shrink-0 items-center justify-center rounded-full text-3xl font-bold text-white ring-4 ring-indigo-500/20 shadow-lg"
                style={{ backgroundColor: bgInitialsColor }}
              >
                {getInitials(companion.name)}
              </div>
            )}

            <div className="flex flex-col min-w-0">
              <h3 className="text-2xl font-bold text-gray-100 truncate">
                {companion.name}
              </h3>
              {companion.persona ? (
                <span className="mt-1 text-sm font-medium text-indigo-400">
                  {companion.persona}
                </span>
              ) : null}
              {companion.description ? (
                <p className="mt-3 text-sm leading-relaxed text-gray-300">
                  {companion.description}
                </p>
              ) : (
                <p className="mt-3 text-sm italic text-gray-500">
                  No description provided.
                </p>
              )}
            </div>
          </div>

          {/* Backstory Panel */}
          <div className="rounded-xl border border-gray-800 bg-gray-800/20 p-6">
            <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200 uppercase tracking-wider mb-3">
              <svg
                className="h-4 w-4 text-indigo-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
                />
              </svg>
              Backstory
            </h4>
            {companion.backstory ? (
              <p className="text-sm leading-relaxed text-gray-300 border-l-2 border-indigo-500/40 pl-4 py-0.5 whitespace-pre-wrap">
                {companion.backstory}
              </p>
            ) : (
              <p className="text-sm italic text-gray-500 border-l-2 border-gray-700 pl-4 py-0.5">
                No backstory has been written for this companion.
              </p>
            )}
          </div>

          {/* Personality Sliders Panel */}
          <div className="rounded-xl border border-gray-800 bg-gray-800/20 p-6">
            <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200 uppercase tracking-wider mb-4">
              <svg
                className="h-4 w-4 text-indigo-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"
                />
              </svg>
              Personality Dimensions
            </h4>
            <div className="grid gap-4 sm:grid-cols-2">
              {PERSONALITY_DIMS.map((dim) => {
                const val = sliders[dim] ?? 0.5;
                const percentage = Math.round(val * 100);
                return (
                  <div
                    key={dim}
                    className="flex flex-col gap-1.5 rounded-lg bg-gray-950/20 p-3.5 border border-gray-800/50"
                  >
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-medium capitalize text-gray-300">
                        {fmtLabel(dim)}
                      </span>
                      <span className="font-mono font-semibold text-indigo-400">
                        {percentage}%
                      </span>
                    </div>
                    <div className="h-2 w-full rounded bg-gray-950/60 overflow-hidden">
                      <div
                        className="h-full rounded bg-gradient-to-r from-indigo-500 to-violet-500 transition-all duration-500 ease-out"
                        style={{ width: `${percentage}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Behavioral Style and Topics */}
          <div className="grid gap-6 sm:grid-cols-2">
            {/* Behavior Card */}
            <div className="rounded-xl border border-gray-800 bg-gray-800/20 p-6">
              <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200 uppercase tracking-wider mb-4">
                <svg
                  className="h-4 w-4 text-indigo-400"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
                  />
                </svg>
                Behavioral Style
              </h4>
              <div className="flex flex-col gap-3">
                <div className="flex items-center justify-between border-b border-gray-800/50 py-2">
                  <span className="text-xs text-gray-400 font-medium">
                    Humor Style
                  </span>
                  <span className="rounded-md bg-indigo-950/40 border border-indigo-800/50 px-2.5 py-1 text-xs font-semibold text-indigo-300 capitalize">
                    {companion.humor_style ? fmtLabel(companion.humor_style) : "Witty"}
                  </span>
                </div>
                <div className="flex items-center justify-between py-2">
                  <span className="text-xs text-gray-400 font-medium">
                    Communication Style
                  </span>
                  <span className="rounded-md bg-violet-950/40 border border-violet-800/50 px-2.5 py-1 text-xs font-semibold text-violet-300 capitalize font-medium">
                    {companion.communication_style
                      ? fmtLabel(companion.communication_style)
                      : "Concise"}
                  </span>
                </div>
              </div>
            </div>

            {/* Favorite Topics Card */}
            <div className="rounded-xl border border-gray-800 bg-gray-800/20 p-6">
              <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200 uppercase tracking-wider mb-4">
                <svg
                  className="h-4 w-4 text-indigo-400"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14"
                  />
                </svg>
                Favorite Topics
              </h4>
              {companion.favorite_topics && companion.favorite_topics.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {companion.favorite_topics.map((topic) => (
                    <span
                      key={topic}
                      className="rounded-full bg-gray-800/80 border border-gray-700 px-3 py-1 text-xs font-medium text-gray-300 transition duration-200 hover:border-gray-500 hover:text-white"
                    >
                      {topic}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-xs italic text-gray-500 leading-relaxed">
                  No specific favorite topics declared.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

export default CompanionInfoPanel;
