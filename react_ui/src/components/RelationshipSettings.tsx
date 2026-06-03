import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  getRelationshipConfig,
  updateRelationshipConfig,
} from "../api/client";
import type { ApiError, RelationshipConfig } from "../types";

interface RelationshipSettingsProps {
  sessionId: string;
}

const relationshipTypes: RelationshipConfig["type"][] = [
  "friend",
  "mentor",
  "peer",
  "partner",
  "custom",
];

const tones: RelationshipConfig["tone"][] = [
  "warm",
  "neutral",
  "formal",
  "playful",
  "sarcastic",
];

const contentLevels: RelationshipConfig["content_level"][] = [
  "light",
  "moderate",
  "deep",
  "intellectual",
];

const relationshipSliders = [
  "directness",
  "warmth",
  "initiative_level",
] as const;

const emptyConfig: RelationshipConfig = {
  type: "friend",
  tone: "warm",
  directness: 0.5,
  warmth: 0.5,
  initiative_level: 0.5,
  content_level: "moderate",
};

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

function formatLabel(value: string) {
  return value.replace(/_/g, " ");
}

export function RelationshipSettings({ sessionId }: RelationshipSettingsProps) {
  const [formData, setFormData] = useState<RelationshipConfig>(emptyConfig);
  const [isFetching, setIsFetching] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function loadConfig() {
      setIsFetching(true);
      setError(null);

      try {
        const config = await getRelationshipConfig(sessionId);

        if (isMounted) {
          setFormData(config);
        }
      } catch (configError) {
        console.error("Failed to load relationship config", configError);

        if (isMounted) {
          setError(getErrorDetail(configError));
        }
      } finally {
        if (isMounted) {
          setIsFetching(false);
        }
      }
    }

    loadConfig();

    return () => {
      isMounted = false;
    };
  }, [sessionId]);

  const handleTypeChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setFormData((currentData) => ({
      ...currentData,
      type: event.target.value as RelationshipConfig["type"],
    }));
  };

  const handleToneChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setFormData((currentData) => ({
      ...currentData,
      tone: event.target.value as RelationshipConfig["tone"],
    }));
  };

  const handleContentLevelChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setFormData((currentData) => ({
      ...currentData,
      content_level: event.target.value as RelationshipConfig["content_level"],
    }));
  };

  const handleSliderChange =
    (field: (typeof relationshipSliders)[number]) =>
    (event: ChangeEvent<HTMLInputElement>) => {
      setFormData((currentData) => ({
        ...currentData,
        [field]: Number(event.target.value),
      }));
    };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);

    try {
      const config = await updateRelationshipConfig(sessionId, formData);
      setFormData(config);
    } catch (configError) {
      console.error("Failed to update relationship config", configError);
      setError(getErrorDetail(configError));
    } finally {
      setIsSaving(false);
    }
  };

  if (isFetching) {
    return (
      <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
        <div className="text-sm text-gray-400">
          Loading relationship settings...
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <form className="flex flex-col gap-5" onSubmit={handleSubmit}>
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Relationship</h2>
          <p className="mt-1 text-sm text-gray-400">
            Configure how the companion relates to this session.
          </p>
        </div>

        {error ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        <div className="grid gap-4 md:grid-cols-3">
          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Type
            <select
              value={formData.type}
              disabled={isSaving}
              className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              onChange={handleTypeChange}
            >
              {relationshipTypes.map((type) => (
                <option key={type} value={type}>
                  {formatLabel(type)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Tone
            <select
              value={formData.tone}
              disabled={isSaving}
              className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              onChange={handleToneChange}
            >
              {tones.map((tone) => (
                <option key={tone} value={tone}>
                  {formatLabel(tone)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Content level
            <select
              value={formData.content_level}
              disabled={isSaving}
              className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              onChange={handleContentLevelChange}
            >
              {contentLevels.map((level) => (
                <option key={level} value={level}>
                  {formatLabel(level)}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          {relationshipSliders.map((field) => {
            const value = formData[field];

            return (
              <label
                key={field}
                className="flex flex-col gap-2 text-sm font-medium text-gray-200"
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="capitalize">{formatLabel(field)}</span>
                  <span className="font-mono text-xs text-gray-400">
                    {value.toFixed(2)}
                  </span>
                </span>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={value}
                  disabled={isSaving}
                  className="h-2 w-full cursor-pointer accent-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
                  onChange={handleSliderChange(field)}
                />
              </label>
            );
          })}
        </div>

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={isSaving}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
          >
            {isSaving ? "Saving..." : "Save relationship"}
          </button>
        </div>
      </form>
    </section>
  );
}

export default RelationshipSettings;
