import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  getCharacterProfile,
  updateCharacterProfile,
} from "../api/client";
import type { ApiError, CharacterProfile } from "../types";

interface CharacterCreatorProps {
  sessionId: string;
}

const personalityDimensions = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
  "curiosity",
  "creativity",
] as const;

const humorStyles: CharacterProfile["humor_style"][] = [
  "dry",
  "witty",
  "silly",
  "dark",
  "none",
];

const communicationStyles: CharacterProfile["communication_style"][] = [
  "verbose",
  "concise",
  "storyteller",
  "question_driven",
  "aphoristic",
];

const defaultPersonalitySliders = personalityDimensions.reduce<
  Record<string, number>
>((sliders, dimension) => {
  sliders[dimension] = 0.5;
  return sliders;
}, {});

const emptyProfile: CharacterProfile = {
  name: "",
  backstory: "",
  personality_sliders: defaultPersonalitySliders,
  humor_style: "witty",
  communication_style: "concise",
  favorite_topics: [],
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

function normalizeProfile(profile: CharacterProfile): CharacterProfile {
  return {
    ...profile,
    personality_sliders: {
      ...defaultPersonalitySliders,
      ...profile.personality_sliders,
    },
    favorite_topics: profile.favorite_topics ?? [],
  };
}

export function CharacterCreator({ sessionId }: CharacterCreatorProps) {
  const [formData, setFormData] = useState<CharacterProfile>(emptyProfile);
  const [topicInput, setTopicInput] = useState("");
  const [isFetching, setIsFetching] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function loadProfile() {
      setIsFetching(true);
      setError(null);

      try {
        const profile = await getCharacterProfile(sessionId);

        if (isMounted) {
          setFormData(normalizeProfile(profile));
        }
      } catch (profileError) {
        console.error("Failed to load character profile", profileError);

        if (isMounted) {
          setError(getErrorDetail(profileError));
        }
      } finally {
        if (isMounted) {
          setIsFetching(false);
        }
      }
    }

    loadProfile();

    return () => {
      isMounted = false;
    };
  }, [sessionId]);

  const handleFieldChange =
    (field: keyof Pick<CharacterProfile, "name" | "backstory">) =>
    (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setFormData((currentData) => ({
        ...currentData,
        [field]: event.target.value,
      }));
    };

  const handleSliderChange =
    (dimension: (typeof personalityDimensions)[number]) =>
    (event: ChangeEvent<HTMLInputElement>) => {
      const value = Number(event.target.value);

      setFormData((currentData) => ({
        ...currentData,
        personality_sliders: {
          ...currentData.personality_sliders,
          [dimension]: value,
        },
      }));
    };

  const handleHumorStyleChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setFormData((currentData) => ({
      ...currentData,
      humor_style: event.target.value as CharacterProfile["humor_style"],
    }));
  };

  const handleCommunicationStyleChange = (
    event: ChangeEvent<HTMLSelectElement>,
  ) => {
    setFormData((currentData) => ({
      ...currentData,
      communication_style: event.target
        .value as CharacterProfile["communication_style"],
    }));
  };

  const handleAddTopic = () => {
    const topic = topicInput.trim();

    if (!topic || formData.favorite_topics.includes(topic)) {
      return;
    }

    setFormData((currentData) => ({
      ...currentData,
      favorite_topics: [...currentData.favorite_topics, topic],
    }));
    setTopicInput("");
  };

  const handleRemoveTopic = (topic: string) => {
    setFormData((currentData) => ({
      ...currentData,
      favorite_topics: currentData.favorite_topics.filter(
        (currentTopic) => currentTopic !== topic,
      ),
    }));
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);

    try {
      const profile = await updateCharacterProfile(sessionId, formData);
      setFormData(normalizeProfile(profile));
    } catch (profileError) {
      console.error("Failed to update character profile", profileError);
      setError(getErrorDetail(profileError));
    } finally {
      setIsSaving(false);
    }
  };

  if (isFetching) {
    return (
      <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
        <div className="text-sm text-gray-400">Loading character profile...</div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <form className="flex flex-col gap-5" onSubmit={handleSubmit}>
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Character</h2>
          <p className="mt-1 text-sm text-gray-400">
            Configure the companion profile for this session.
          </p>
        </div>

        {error ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
          Name
          <input
            type="text"
            value={formData.name}
            disabled={isSaving}
            className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
            placeholder="Companion name"
            onChange={handleFieldChange("name")}
          />
        </label>

        <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
          Backstory
          <textarea
            rows={4}
            value={formData.backstory}
            disabled={isSaving}
            className="resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
            placeholder="Background, history, and defining context"
            onChange={handleFieldChange("backstory")}
          />
        </label>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Humor style
            <select
              value={formData.humor_style}
              disabled={isSaving}
              className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              onChange={handleHumorStyleChange}
            >
              {humorStyles.map((style) => (
                <option key={style} value={style}>
                  {formatLabel(style)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Communication style
            <select
              value={formData.communication_style}
              disabled={isSaving}
              className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              onChange={handleCommunicationStyleChange}
            >
              {communicationStyles.map((style) => (
                <option key={style} value={style}>
                  {formatLabel(style)}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-gray-200">Personality</h3>
          <div className="grid gap-4 md:grid-cols-2">
            {personalityDimensions.map((dimension) => {
              const value = formData.personality_sliders[dimension] ?? 0.5;

              return (
                <label
                  key={dimension}
                  className="flex flex-col gap-2 text-sm font-medium text-gray-200"
                >
                  <span className="flex items-center justify-between gap-3">
                    <span className="capitalize">{formatLabel(dimension)}</span>
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
                    onChange={handleSliderChange(dimension)}
                  />
                </label>
              );
            })}
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <label className="text-sm font-medium text-gray-200" htmlFor="topic">
            Favorite topics
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              id="topic"
              type="text"
              value={topicInput}
              disabled={isSaving}
              className="min-w-0 flex-1 rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              placeholder="Add a topic"
              onChange={(event) => setTopicInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  handleAddTopic();
                }
              }}
            />
            <button
              type="button"
              disabled={isSaving || topicInput.trim().length === 0}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
              onClick={handleAddTopic}
            >
              Add
            </button>
          </div>
          {formData.favorite_topics.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {formData.favorite_topics.map((topic) => (
                <span
                  key={topic}
                  className="inline-flex items-center gap-2 rounded-full border border-gray-600 bg-gray-700 px-3 py-1 text-sm text-gray-100"
                >
                  {topic}
                  <button
                    type="button"
                    disabled={isSaving}
                    className="text-gray-400 transition hover:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-60"
                    aria-label={`Remove ${topic}`}
                    onClick={() => handleRemoveTopic(topic)}
                  >
                    x
                  </button>
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={isSaving}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
          >
            {isSaving ? "Saving..." : "Save character"}
          </button>
        </div>
      </form>
    </section>
  );
}

export default CharacterCreator;
