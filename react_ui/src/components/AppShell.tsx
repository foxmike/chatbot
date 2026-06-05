import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { User } from "../api/client";

export type AppPanelKey =
  | "chat"
  | "settings"
  | "memory"
  | "timeline"
  | "rooms"
  | "voice"
  | "admin";

interface AppShellProps {
  user: User;
  activeSessionTitle?: string;
  companionSwitcher?: ReactNode;
  sidebar: ReactNode;
  chatPanel: ReactNode;
  panels: Record<Exclude<AppPanelKey, "chat">, ReactNode>;
}

const panelTabs: { key: AppPanelKey; label: string }[] = [
  { key: "chat", label: "Chat" },
  { key: "settings", label: "Settings" },
  { key: "memory", label: "Memory" },
  { key: "timeline", label: "Timeline" },
  { key: "rooms", label: "Rooms" },
  { key: "voice", label: "Voice" },
  { key: "admin", label: "Admin" },
];

export function AppShell({
  user,
  activeSessionTitle,
  companionSwitcher,
  sidebar,
  chatPanel,
  panels,
}: AppShellProps) {
  const [activePanel, setActivePanel] = useState<AppPanelKey>("chat");

  const activeContent = useMemo(() => {
    if (activePanel === "chat") {
      return chatPanel;
    }

    return panels[activePanel];
  }, [activePanel, chatPanel, panels]);

  return (
    <div className="flex h-screen bg-gray-900 text-gray-100">
      {sidebar}

      <main className="flex min-w-0 flex-1 flex-col bg-gray-900">
        <header className="shrink-0 border-b border-gray-700 bg-gray-900/95">
          <div className="flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex min-w-0 flex-col gap-0.5">
              <p className="truncate text-xs font-medium uppercase tracking-wide text-gray-500">
                {user.username}
              </p>
              {companionSwitcher}
              <h1 className="truncate text-sm text-gray-400">
                {activeSessionTitle ?? "No active chat"}
              </h1>
            </div>

            <nav
              aria-label="App panels"
              className="flex max-w-full gap-1 overflow-x-auto rounded-md border border-gray-700 bg-gray-950/60 p-1"
              role="tablist"
            >
              {panelTabs.map((tab) => {
                const isActive = tab.key === activePanel;

                return (
                  <button
                    key={tab.key}
                    type="button"
                    aria-selected={isActive}
                    className={`h-9 shrink-0 rounded px-3 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-indigo-500/70 ${
                      isActive
                        ? "bg-indigo-600 text-white"
                        : "text-gray-300 hover:bg-gray-800 hover:text-gray-100"
                    }`}
                    onClick={() => setActivePanel(tab.key)}
                    role="tab"
                  >
                    {tab.label}
                  </button>
                );
              })}
            </nav>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-hidden">{activeContent}</div>
      </main>
    </div>
  );
}

export default AppShell;
