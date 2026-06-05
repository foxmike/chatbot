import type { User } from "../api/client";

interface SettingsPanelProps {
  user: User;
  activeSessionId: string | null;
  activeSessionTitle?: string;
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

function BoundaryItem({ label, status }: { label: string; status: string }) {
  return (
    <li className="flex items-center justify-between gap-3 rounded border border-gray-700 bg-gray-900 px-3 py-3">
      <span className="text-sm font-medium text-gray-100">{label}</span>
      <span className="shrink-0 rounded border border-amber-800 bg-amber-950/50 px-2 py-1 text-xs font-medium text-amber-200">
        {status}
      </span>
    </li>
  );
}

export function SettingsPanel({
  user,
  activeSessionId,
  activeSessionTitle,
  sessionCount,
}: SettingsPanelProps) {
  return (
    <section className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto flex max-w-5xl flex-col gap-5">
        <div>
          <h2 className="text-xl font-semibold text-gray-100">Settings</h2>
          <p className="mt-1 text-sm text-gray-400">
            Account and active session context.
          </p>
        </div>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]">
          <section className="rounded-lg border border-gray-700 bg-gray-800 p-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              Current User
            </h3>
            <dl className="mt-3">
              <DetailRow label="Username" value={user.username} />
              <DetailRow label="Email" value={user.email} />
              <DetailRow label="User ID" value={user.user_id} />
            </dl>
          </section>

          <section className="rounded-lg border border-gray-700 bg-gray-800 p-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              Active Session
            </h3>
            <dl className="mt-3">
              <DetailRow
                label="Title"
                value={activeSessionTitle ?? "No active chat"}
              />
              <DetailRow
                label="Session ID"
                value={activeSessionId ?? "Unavailable"}
              />
              <DetailRow label="Chats" value={String(sessionCount)} />
            </dl>
          </section>
        </div>

        <section className="rounded-lg border border-gray-700 bg-gray-800 p-5">
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              Protected Configuration
            </h3>
            <p className="text-sm text-gray-400">
              Relationship, character, and identity controls stay inactive until
              session ownership is verified by their backend endpoints.
            </p>
          </div>

          <ul className="mt-4 grid gap-3 sm:grid-cols-3">
            <BoundaryItem label="Relationship" status="Guarded" />
            <BoundaryItem label="Character" status="Guarded" />
            <BoundaryItem label="Identity" status="Guarded" />
          </ul>
        </section>
      </div>
    </section>
  );
}

export default SettingsPanel;
