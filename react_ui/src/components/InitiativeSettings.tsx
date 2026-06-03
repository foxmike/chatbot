import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  fetchInitiative,
  updateInitiative,
} from "../api/client";
import type {
  InitiativeLevel,
  InitiativeSettings as InitiativeSettingsType,
} from "../api/client";
import type { ApiError } from "../types";

interface InitiativeSettingsProps {
  userId: string;
}

const levels: { label: string; value: InitiativeLevel }[] = [
  { label: "Never Proactive", value: "never_proactive" },
  { label: "Reminders Only", value: "reminders_only" },
  { label: "Gentle Check-ins", value: "gentle_checkins" },
  { label: "Frequent", value: "frequent" },
  { label: "High-Energy", value: "high_energy" },
];

function getErrorDetail(error: unknown) {
  if (
    typeof error === "object" &&
    error !== null &&
    "detail" in error &&
    typeof (error as ApiError).detail === "string"
  ) {
    return (error as ApiError).detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Something went wrong. Please try again.";
}

export function InitiativeSettings({ userId }: InitiativeSettingsProps) {
  const [settings, setSettings] = useState<InitiativeSettingsType | null>(null);
  const [level, setLevel] = useState<InitiativeLevel>("gentle_checkins");
  const [quietHoursEnabled, setQuietHoursEnabled] = useState(false);
  const [quietHoursStart, setQuietHoursStart] = useState("22:00");
  const [quietHoursEnd, setQuietHoursEnd] = useState("08:00");
  const [isFetching, setIsFetching] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function loadSettings() {
      setIsFetching(true);
      setError(null);

      try {
        const result = await fetchInitiative(userId);

        if (isMounted) {
          setSettings(result);
          setLevel(result.level);
          setQuietHoursEnabled(result.quiet_hours_start !== null);
          if (result.quiet_hours_start) {
            setQuietHoursStart(result.quiet_hours_start);
          }
          if (result.quiet_hours_end) {
            setQuietHoursEnd(result.quiet_hours_end);
          }
        }
      } catch (err) {
        console.error("Failed to load initiative settings", err);
        if (isMounted) {
          setError(getErrorDetail(err));
        }
      } finally {
        if (isMounted) {
          setIsFetching(false);
        }
      }
    }

    loadSettings();

    return () => {
      isMounted = false;
    };
  }, [userId]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    setSuccessMessage(null);

    try {
      const updated = await updateInitiative(userId, {
        level,
        quiet_hours_start: quietHoursEnabled ? quietHoursStart : null,
        quiet_hours_end: quietHoursEnabled ? quietHoursEnd : null,
      });
      setSettings(updated);
      setSuccessMessage("Initiative settings saved.");
    } catch (err) {
      console.error("Failed to save initiative settings", err);
      setError(getErrorDetail(err));
    } finally {
      setIsSaving(false);
    }
  };

  if (isFetching) {
    return (
      <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
        <div className="text-sm text-gray-400">
          Loading initiative settings...
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <form className="flex flex-col gap-5" onSubmit={handleSubmit}>
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Initiative</h2>
          <p className="mt-1 text-sm text-gray-400">
            Control how proactively your companion reaches out to you.
          </p>
        </div>

        {error ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        {successMessage ? (
          <div className="rounded-md border border-emerald-800 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-200">
            {successMessage}
          </div>
        ) : null}

        <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
          Initiative level
          <select
            value={level}
            disabled={isSaving}
            className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
            onChange={(e: ChangeEvent<HTMLSelectElement>) =>
              setLevel(e.target.value as InitiativeLevel)
            }
          >
            {levels.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>

        <fieldset className="flex flex-col gap-3 rounded-lg border border-gray-700 bg-gray-900/60 p-4">
          <legend className="text-sm font-medium text-gray-200">
            Quiet Hours
          </legend>

          <label className="flex items-center gap-3 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={quietHoursEnabled}
              disabled={isSaving}
              className="h-4 w-4 rounded border-gray-600 bg-gray-900 accent-indigo-500 focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-60"
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setQuietHoursEnabled(e.target.checked)
              }
            />
            Enable quiet hours
          </label>

          {quietHoursEnabled ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Start time
                <input
                  type="time"
                  value={quietHoursStart}
                  disabled={isSaving}
                  className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setQuietHoursStart(e.target.value)
                  }
                />
              </label>
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                End time
                <input
                  type="time"
                  value={quietHoursEnd}
                  disabled={isSaving}
                  className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setQuietHoursEnd(e.target.value)
                  }
                />
              </label>
            </div>
          ) : null}
        </fieldset>

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={isSaving}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
          >
            {isSaving ? "Saving..." : "Save"}
          </button>
        </div>
      </form>
    </section>
  );
}

export default InitiativeSettings;