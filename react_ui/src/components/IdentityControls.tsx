import { useEffect, useMemo, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  evolveIdentity,
  getIdentityState,
  resetIdentityMood,
  type IdentityEvolutionContext,
} from "../api/client";
import type { ApiError, IdentityState } from "../types";

interface IdentityControlsProps {
  sessionId: string;
}

type SentimentOption = "neutral" | "positive" | "negative";

const toneOptions = ["", "warm", "gentle", "focused", "playful", "neutral"];

const defaultContext = {
  topic: "",
  tone: "",
  sentiment: "neutral" as SentimentOption,
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

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "None";
  }

  if (typeof value === "number") {
    return value.toFixed(2);
  }

  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }

  if (Array.isArray(value)) {
    return value.length > 0 ? value.map(formatValue).join(", ") : "None";
  }

  if (typeof value === "object") {
    return JSON.stringify(value);
  }

  return String(value);
}

function buildEvolutionContext(
  topic: string,
  tone: string,
  sentiment: SentimentOption,
): IdentityEvolutionContext {
  const context: IdentityEvolutionContext = {};
  const trimmedTopic = topic.trim();

  if (trimmedTopic) {
    context.topic = trimmedTopic;
    context.current_interest = trimmedTopic;
  }

  if (tone) {
    context.tone = tone;
  }

  if (sentiment !== "neutral") {
    context.sentiment_label = sentiment;
  }

  return context;
}

function StateTier({
  title,
  values,
}: {
  title: string;
  values: Record<string, unknown>;
}) {
  const entries = Object.entries(values);

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-900 p-4">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
        {title}
      </h3>
      {entries.length > 0 ? (
        <dl className="mt-3 divide-y divide-gray-800">
          {entries.map(([key, value]) => (
            <div
              key={key}
              className="grid gap-1 py-2 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)]"
            >
              <dt className="text-sm capitalize text-gray-400">
                {formatLabel(key)}
              </dt>
              <dd className="min-w-0 break-words text-sm font-medium text-gray-100">
                {formatValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-3 text-sm text-gray-400">No values recorded.</p>
      )}
    </section>
  );
}

export function IdentityControls({ sessionId }: IdentityControlsProps) {
  const [identityState, setIdentityState] = useState<IdentityState | null>(null);
  const [topic, setTopic] = useState(defaultContext.topic);
  const [tone, setTone] = useState(defaultContext.tone);
  const [sentiment, setSentiment] = useState<SentimentOption>(
    defaultContext.sentiment,
  );
  const [isFetching, setIsFetching] = useState(true);
  const [isEvolving, setIsEvolving] = useState(false);
  const [isResettingMood, setIsResettingMood] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const evolutionContext = useMemo(
    () => buildEvolutionContext(topic, tone, sentiment),
    [sentiment, tone, topic],
  );
  const hasEvolutionContext = Object.keys(evolutionContext).length > 0;

  useEffect(() => {
    let isMounted = true;

    async function loadIdentityState() {
      setIsFetching(true);
      setError(null);

      try {
        const state = await getIdentityState(sessionId);

        if (isMounted) {
          setIdentityState(state);
        }
      } catch (identityError) {
        console.error("Failed to load identity state", identityError);

        if (isMounted) {
          setError(getErrorDetail(identityError));
        }
      } finally {
        if (isMounted) {
          setIsFetching(false);
        }
      }
    }

    loadIdentityState();

    return () => {
      isMounted = false;
    };
  }, [sessionId]);

  const isBusy = isFetching || isEvolving || isResettingMood;

  const handleTopicChange = (event: ChangeEvent<HTMLInputElement>) => {
    setTopic(event.target.value);
  };

  const handleToneChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setTone(event.target.value);
  };

  const handleSentimentChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setSentiment(event.target.value as SentimentOption);
  };

  const handleEvolve = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!hasEvolutionContext) {
      return;
    }

    setIsEvolving(true);
    setError(null);

    try {
      const state = await evolveIdentity(sessionId, evolutionContext);
      setIdentityState(state);
    } catch (identityError) {
      console.error("Failed to evolve identity state", identityError);
      setError(getErrorDetail(identityError));
    } finally {
      setIsEvolving(false);
    }
  };

  const handleResetMood = async () => {
    setIsResettingMood(true);
    setError(null);

    try {
      const state = await resetIdentityMood(sessionId);
      setIdentityState(state);
    } catch (identityError) {
      console.error("Failed to reset identity mood", identityError);
      setError(getErrorDetail(identityError));
    } finally {
      setIsResettingMood(false);
    }
  };

  if (isFetching) {
    return (
      <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
        <div className="text-sm text-gray-400">Loading identity state...</div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <div className="flex flex-col gap-5">
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Identity</h2>
          <p className="mt-1 text-sm text-gray-400">
            Review stable traits, slow drift, and current mood for this session.
          </p>
        </div>

        {error ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        {identityState ? (
          <div className="grid gap-4 xl:grid-cols-3">
            <StateTier title="Fixed" values={identityState.fixed} />
            <StateTier title="Slow" values={identityState.slow} />
            <StateTier title="Mood" values={identityState.mood} />
          </div>
        ) : (
          <div className="rounded-md border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
            Identity state is unavailable for this session.
          </div>
        )}

        <form
          className="grid gap-4 rounded-lg border border-gray-700 bg-gray-900 p-4 lg:grid-cols-[minmax(0,1fr)_160px_160px_auto]"
          onSubmit={handleEvolve}
        >
          <label className="flex min-w-0 flex-col gap-2 text-sm font-medium text-gray-200">
            Topic
            <input
              type="text"
              value={topic}
              disabled={isBusy}
              className="min-w-0 rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              placeholder="Current focus"
              onChange={handleTopicChange}
            />
          </label>

          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Tone
            <select
              value={tone}
              disabled={isBusy}
              className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              onChange={handleToneChange}
            >
              {toneOptions.map((option) => (
                <option key={option || "auto"} value={option}>
                  {option ? formatLabel(option) : "Auto"}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
            Sentiment
            <select
              value={sentiment}
              disabled={isBusy}
              className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm capitalize text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              onChange={handleSentimentChange}
            >
              <option value="neutral">Neutral</option>
              <option value="positive">Positive</option>
              <option value="negative">Negative</option>
            </select>
          </label>

          <div className="flex flex-col justify-end gap-2 sm:flex-row lg:flex-col">
            <button
              type="submit"
              disabled={isBusy || !hasEvolutionContext}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-900 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
            >
              {isEvolving ? "Applying..." : "Apply evolution"}
            </button>
            <button
              type="button"
              disabled={isBusy}
              className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-100 transition hover:border-gray-500 hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-900 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={handleResetMood}
            >
              {isResettingMood ? "Resetting..." : "Reset mood"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}

export default IdentityControls;
