import { useEffect, useState } from "react";
import type { Companion, User } from "../api/client";
import { listCompanions, listSessions } from "../api/client";
import type { Session } from "../types";
import { RelationshipSettings } from "./RelationshipSettings";
import { CompanionEditor } from "./CompanionEditor";
import { IdentityControls } from "./IdentityControls";

type Tab = "account" | "companion" | "session";

interface SettingsPanelProps {
  user: User;
  selectedCompanion: Companion;
  sessions: Session[];
  activeSessionId: string | null;
  sessionCount: number;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 border-b border-gray-800 py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between">
      <dt className="text-sm text-gray-400">{label}</dt>
      <dd className="min-w-0 break-words text-sm font-medium text-gray-100">
        {value}
      </dd>
    </div>
  );
}

function TabButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={[
        "-mb-px px-4 py-2.5 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500/60",
        active
          ? "border-b-2 border-indigo-500 text-indigo-400"
          : "border-b-2 border-transparent text-gray-400 hover:text-gray-200",
      ].join(" ")}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

export function SettingsPanel({
  user,
  selectedCompanion,
  sessions: propSessions,
  activeSessionId,
  sessionCount,
}: SettingsPanelProps) {
  const [activeTab, setActiveTab] = useState<Tab>("account");
  const [companions, setCompanions] = useState<Companion[]>([]);

  // The companion whose data the panel is currently showing (may differ from
  // the globally active companion — the user can inspect any companion here
  // without affecting the chat).
  const [panelCompanion, setPanelCompanion] = useState<Companion>(selectedCompanion);

  // Sessions loaded for a non-global companion selected in the dropdown.
  const [localSessions, setLocalSessions] = useState<Session[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  // Load the companion list for the dropdown.
  useEffect(() => {
    listCompanions()
      .then((list) => setCompanions(list))
      .catch(() => {});
  }, []);

  // When the globally active companion changes (user switched via the header
  // CompanionSwitcher), reset the panel to follow it.
  useEffect(() => {
    setPanelCompanion(selectedCompanion);
    setLocalSessions([]);
  }, [selectedCompanion]);

  // Decide which session to use for Companion/Session tabs.
  // If the panel companion matches the global active companion, read directly
  // from the prop sessions (always up-to-date); otherwise use what we loaded.
  const isGlobalCompanion =
    panelCompanion.companion_id === selectedCompanion.companion_id;

  const panelSessions = isGlobalCompanion ? propSessions : localSessions;
  const panelSession =
    (isGlobalCompanion
      ? panelSessions.find((s) => s.session_id === activeSessionId)
      : undefined) ??
    panelSessions[0] ??
    null;

  const handleCompanionChange = async (companionId: string) => {
    if (companionId === panelCompanion.companion_id) return;

    // Switch back to global companion — no extra fetch needed.
    if (companionId === selectedCompanion.companion_id) {
      setPanelCompanion(selectedCompanion);
      setLocalSessions([]);
      return;
    }

    const companion = companions.find((c) => c.companion_id === companionId);
    if (!companion) return;

    setPanelCompanion(companion);
    setLocalSessions([]);
    setSessionsLoading(true);
    try {
      const loaded = await listSessions(companionId);
      setLocalSessions(loaded);
    } catch {
      setLocalSessions([]);
    } finally {
      setSessionsLoading(false);
    }
  };

  const companionList = companions.length > 0 ? companions : [selectedCompanion];

  return (
    <section className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto flex max-w-5xl flex-col gap-5">
        {/* ── Header ─────────────────────────────────────────────────── */}
        <div>
          <h2 className="text-xl font-semibold text-gray-100">Settings</h2>
          <p className="mt-1 text-sm text-gray-400">
            Manage your account, companion profile, and session.
          </p>
        </div>

        {/* ── Companion selector ─────────────────────────────────────── */}
        <div className="flex items-center gap-3">
          <label
            htmlFor="settings-companion-select"
            className="shrink-0 text-sm font-medium text-gray-400"
          >
            Companion
          </label>
          <select
            id="settings-companion-select"
            value={panelCompanion.companion_id}
            className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-1.5 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            onChange={(e) => handleCompanionChange(e.target.value)}
          >
            {companionList.map((c) => (
              <option key={c.companion_id} value={c.companion_id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>

        {/* ── Tabs ───────────────────────────────────────────────────── */}
        <div className="flex border-b border-gray-700">
          <TabButton
            label="Account"
            active={activeTab === "account"}
            onClick={() => setActiveTab("account")}
          />
          <TabButton
            label="Companion"
            active={activeTab === "companion"}
            onClick={() => setActiveTab("companion")}
          />
          <TabButton
            label="Session"
            active={activeTab === "session"}
            onClick={() => setActiveTab("session")}
          />
        </div>

        {/* ── Context label ──────────────────────────────────────────── */}
        {activeTab === "companion" && (
          <p className="text-xs text-gray-500">
            Editing: {panelCompanion.name}
          </p>
        )}
        {activeTab === "session" && (
          <p className="text-xs text-gray-500">
            {panelSession
              ? `Session: ${panelCompanion.name}: ${panelSession.title}`
              : `No active session for ${panelCompanion.name}`}
          </p>
        )}

        {/* ── Account tab ────────────────────────────────────────────── */}
        {activeTab === "account" && (
          <section className="rounded-lg border border-gray-700 bg-gray-800 p-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              Account
            </h3>
            <dl className="mt-3">
              <DetailRow label="Username" value={user.username} />
              <DetailRow label="Email" value={user.email} />
              <DetailRow label="User ID" value={user.user_id} />
            </dl>
          </section>
        )}

        {/* ── Companion tab ──────────────────────────────────────────── */}
        {activeTab === "companion" && (
          <CompanionEditor
            companion={panelCompanion}
            showCancel={false}
            onSave={(updated) => {
              setPanelCompanion(updated);
              setCompanions((prev) =>
                prev.map((c) =>
                  c.companion_id === updated.companion_id ? updated : c,
                ),
              );
            }}
          />
        )}

        {/* ── Session tab ────────────────────────────────────────────── */}
        {activeTab === "session" && (
          <>
            {sessionsLoading ? (
              <div className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-sm text-gray-400">
                Loading session data…
              </div>
            ) : panelSession ? (
              <div className="flex flex-col gap-5">
                <section className="rounded-lg border border-gray-700 bg-gray-800 p-5">
                  <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
                    Session
                  </h3>
                  <dl className="mt-3">
                    <DetailRow label="Title" value={panelSession.title} />
                    <DetailRow
                      label="Session ID"
                      value={panelSession.session_id}
                    />
                    <DetailRow
                      label="Total sessions"
                      value={String(sessionCount)}
                    />
                  </dl>
                </section>
                <RelationshipSettings sessionId={panelSession.session_id} />
                <IdentityControls sessionId={panelSession.session_id} />
              </div>
            ) : (
              <section className="rounded-lg border border-gray-700 bg-gray-800 p-5">
                <p className="text-sm text-gray-400">
                  No active session for {panelCompanion.name}. Start a
                  conversation first.
                </p>
              </section>
            )}
          </>
        )}
      </div>
    </section>
  );
}

export default SettingsPanel;
