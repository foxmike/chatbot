import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  getActiveScene,
  createScene,
  updateScene,
  pauseScene,
  resumeScene,
  endScene,
  setScenePersona,
  classifySceneMemory,
} from "../api/client";
import type {
  RoomType,
  SceneData,
  ClassificationResponse,
  ScenePromptResponse,
} from "../api/client";
import type { ApiError } from "../types";

interface SceneControlsProps {
  roomId: string | null;
  roomType: RoomType | null;
}

const TONE_OPTIONS = [
  "neutral",
  "playful",
  "serious",
  "tense",
  "romantic",
  "mysterious",
  "comedic",
  "dramatic",
] as const;

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

function classificationBadgeColor(
  classification: string,
): string {
  switch (classification) {
    case "scene_only":
      return "border-blue-700 bg-blue-900/40 text-blue-200";
    case "durable":
      return "border-green-700 bg-green-900/40 text-green-200";
    case "both":
      return "border-purple-700 bg-purple-900/40 text-purple-200";
    default:
      return "border-gray-600 bg-gray-700 text-gray-300";
  }
}

function intensityColor(value: number): string {
  if (value <= 3) return "text-emerald-400";
  if (value <= 6) return "text-yellow-400";
  return "text-red-400";
}

export function SceneControls({
  roomId,
  roomType,
}: SceneControlsProps) {
  // Active scene state
  const [scene, setScene] = useState<SceneData | null>(null);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Start scene modal
  const [showStartModal, setShowStartModal] = useState(false);
  const [startLocation, setStartLocation] = useState("");
  const [startActivity, setStartActivity] = useState("");
  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // End scene confirmation
  const [showEndConfirm, setShowEndConfirm] = useState(false);
  const [isEnding, setIsEnding] = useState(false);

  // Inline editing
  const [isEditing, setIsEditing] = useState(false);
  const [editLocation, setEditLocation] = useState("");
  const [editActivity, setEditActivity] = useState("");
  const [editTone, setEditTone] = useState("");
  const [editIntensity, setEditIntensity] = useState(5);
  const [editContext, setEditContext] = useState("");
  const [isSavingEdit, setIsSavingEdit] = useState(false);

  // Persona modal
  const [showPersonaModal, setShowPersonaModal] = useState(false);
  const [personaName, setPersonaName] = useState("");
  const [personaDescription, setPersonaDescription] = useState("");
  const [personaTraits, setPersonaTraits] = useState("");
  const [personaVoice, setPersonaVoice] = useState("");
  const [personaBackstory, setPersonaBackstory] = useState("");
  const [personaGoals, setPersonaGoals] = useState("");
  const [personaConstraints, setPersonaConstraints] = useState("");
  const [isSavingPersona, setIsSavingPersona] = useState(false);
  const [personaError, setPersonaError] = useState<string | null>(null);
  const [systemPrompt, setSystemPrompt] = useState<string | null>(null);
  const [showPromptPreview, setShowPromptPreview] = useState(false);

  // Memory classification
  const [classifyText, setClassifyText] = useState("");
  const [classifyResult, setClassifyResult] =
    useState<ClassificationResponse | null>(null);
  const [isClassifying, setIsClassifying] = useState(false);
  const [classifyError, setClassifyError] = useState<string | null>(null);

  // Action error overlay
  const [actionError, setActionError] = useState<string | null>(null);

  const loadScene = async () => {
    if (!roomId) return;

    setIsFetching(true);
    setError(null);

    try {
      const result = await getActiveScene(roomId);
      setScene(result);
    } catch (err) {
      // No active scene is not an error — just show the start page
      const apiErr = err as ApiError;
      if (apiErr?.status_code === 404) {
        setScene(null);
      } else {
        console.error("Failed to load active scene", err);
        setError(getErrorDetail(err));
      }
    } finally {
      setIsFetching(false);
    }
  };

  useEffect(() => {
    if (roomType === "roleplay_scene" && roomId) {
      loadScene();
    } else {
      setScene(null);
      setIsFetching(false);
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, roomType]);

  // ── Start Scene ──────────────────────────────────────────

  const handleStartScene = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!roomId || !startLocation.trim() || !startActivity.trim()) return;

    setIsStarting(true);
    setStartError(null);

    try {
      const result = await createScene(roomId, {
        room_id: roomId,
        location: startLocation.trim(),
        activity: startActivity.trim(),
      });
      setScene(result);
      setShowStartModal(false);
      setStartLocation("");
      setStartActivity("");
    } catch (err) {
      console.error("Failed to start scene", err);
      setStartError(getErrorDetail(err));
    } finally {
      setIsStarting(false);
    }
  };

  // ── Pause / Resume ───────────────────────────────────────

  const handlePauseResume = async () => {
    if (!scene) return;

    setActionError(null);
    setIsSavingEdit(true);

    try {
      const result = scene.is_paused
        ? await resumeScene(scene.scene_id)
        : await pauseScene(scene.scene_id);
      setScene(result);
    } catch (err) {
      console.error("Failed to toggle pause", err);
      setActionError(getErrorDetail(err));
    } finally {
      setIsSavingEdit(false);
    }
  };

  // ── End Scene ────────────────────────────────────────────

  const handleEndScene = async () => {
    if (!scene) return;

    setIsEnding(true);
    setActionError(null);

    try {
      const result = await endScene(scene.scene_id);
      setScene(result);
      setShowEndConfirm(false);
    } catch (err) {
      console.error("Failed to end scene", err);
      setActionError(getErrorDetail(err));
    } finally {
      setIsEnding(false);
    }
  };

  // ── Edit / Update Scene ──────────────────────────────────

  const openEditor = () => {
    if (!scene) return;
    setEditLocation(scene.location);
    setEditActivity(scene.activity);
    setEditTone(scene.tone);
    setEditIntensity(scene.intensity);
    setEditContext(scene.scene_context);
    setIsEditing(true);
  };

  const handleSaveEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!scene || !editLocation.trim() || !editActivity.trim()) return;

    setIsSavingEdit(true);
    setActionError(null);

    try {
      const result = await updateScene(scene.scene_id, {
        location: editLocation.trim(),
        activity: editActivity.trim(),
        tone: editTone,
        intensity: editIntensity,
        scene_context: editContext,
      });
      setScene(result);
      setIsEditing(false);
    } catch (err) {
      console.error("Failed to update scene", err);
      setActionError(getErrorDetail(err));
    } finally {
      setIsSavingEdit(false);
    }
  };

  // ── Persona ──────────────────────────────────────────────

  const handleSavePersona = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!scene) return;

    setIsSavingPersona(true);
    setPersonaError(null);

    try {
      const traits = personaTraits
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t.length > 0);

      const goals = personaGoals
        .split("\n")
        .map((g) => g.trim())
        .filter((g) => g.length > 0);

      const constraints = personaConstraints
        .split("\n")
        .map((c) => c.trim())
        .filter((c) => c.length > 0);

      const result: ScenePromptResponse = await setScenePersona(
        scene.scene_id,
        {
          name: personaName.trim(),
          description: personaDescription.trim(),
          traits,
          voice_style: personaVoice.trim(),
          backstory: personaBackstory.trim() || undefined,
          scene_goals: goals.length > 0 ? goals : undefined,
          constraints: constraints.length > 0 ? constraints : undefined,
        },
      );

      setSystemPrompt(result.system_prompt);
      setShowPromptPreview(true);
      setShowPersonaModal(false);
    } catch (err) {
      console.error("Failed to set persona", err);
      setPersonaError(getErrorDetail(err));
    } finally {
      setIsSavingPersona(false);
    }
  };

  // ── Memory Classification ────────────────────────────────

  const handleClassify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!scene || !classifyText.trim()) return;

    setIsClassifying(true);
    setClassifyError(null);

    try {
      const result = await classifySceneMemory(
        scene.scene_id,
        classifyText.trim(),
      );
      setClassifyResult(result);
    } catch (err) {
      console.error("Failed to classify memory", err);
      setClassifyError(getErrorDetail(err));
    } finally {
      setIsClassifying(false);
    }
  };

  // ── Render ───────────────────────────────────────────────

  if (roomType !== "roleplay_scene") {
    return null;
  }

  if (!roomId) {
    return null;
  }

  if (isFetching) {
    return (
      <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
        <div className="text-sm text-gray-400">Loading scene...</div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
        <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
          {error}
          <button
            type="button"
            className="ml-2 underline hover:no-underline"
            onClick={loadScene}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }

  if (!scene || scene.ended_at) {
    // No active scene — show start scene button
    return (
      <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
        <div className="flex flex-col gap-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-100">Scene</h2>
            <p className="mt-1 text-sm text-gray-400">
              No active scene. Start a new roleplay scene.
            </p>
          </div>

          <button
            type="button"
            className="self-start rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800"
            onClick={() => {
              setStartError(null);
              setShowStartModal(true);
            }}
          >
            Start Scene
          </button>

          {showStartModal ? (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
              <div className="w-full max-w-md rounded-lg border border-gray-700 bg-gray-800 p-6 text-gray-100 shadow-xl">
                <h3 className="text-lg font-semibold">Start Scene</h3>

                {startError ? (
                  <div className="mt-3 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
                    {startError}
                  </div>
                ) : null}

                <form
                  className="mt-4 flex flex-col gap-4"
                  onSubmit={handleStartScene}
                >
                  <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                    Location
                    <input
                      type="text"
                      value={startLocation}
                      disabled={isStarting}
                      className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                      placeholder="Where does the scene take place?"
                      onChange={(e: ChangeEvent<HTMLInputElement>) =>
                        setStartLocation(e.target.value)
                      }
                    />
                  </label>

                  <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                    Activity
                    <input
                      type="text"
                      value={startActivity}
                      disabled={isStarting}
                      className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                      placeholder="What is happening?"
                      onChange={(e: ChangeEvent<HTMLInputElement>) =>
                        setStartActivity(e.target.value)
                      }
                    />
                  </label>

                  <div className="flex justify-end gap-3 pt-2">
                    <button
                      type="button"
                      disabled={isStarting}
                      className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                      onClick={() => {
                        setShowStartModal(false);
                        setStartError(null);
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={
                        isStarting ||
                        !startLocation.trim() ||
                        !startActivity.trim()
                      }
                      className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
                    >
                      {isStarting ? "Starting..." : "Start Scene"}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    );
  }

  // Active scene control panel
  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <div className="flex flex-col gap-5">
        {/* Header */}
        <div>
          <h2 className="text-lg font-semibold text-gray-100">Scene</h2>
          <p className="mt-1 text-sm text-gray-400">
            Manage the active roleplay scene.
          </p>
        </div>

        {actionError ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {actionError}
            <button
              type="button"
              className="ml-2 underline hover:no-underline"
              onClick={() => setActionError(null)}
            >
              Dismiss
            </button>
          </div>
        ) : null}

        {/* Scene Summary Bar */}
        {isEditing ? (
          <form
            className="flex flex-col gap-4 rounded-lg border border-gray-700 bg-gray-900/60 p-4"
            onSubmit={handleSaveEdit}
          >
            <h3 className="text-sm font-semibold text-gray-200">Edit Scene</h3>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Location
                <input
                  type="text"
                  value={editLocation}
                  disabled={isSavingEdit}
                  className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setEditLocation(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Activity
                <input
                  type="text"
                  value={editActivity}
                  disabled={isSavingEdit}
                  className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setEditActivity(e.target.value)
                  }
                />
              </label>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Tone
                <select
                  value={editTone}
                  disabled={isSavingEdit}
                  className="rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                    setEditTone(e.target.value)
                  }
                >
                  {TONE_OPTIONS.map((tone) => (
                    <option key={tone} value={tone}>
                      {tone}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                <span className="flex items-center justify-between">
                  <span>Intensity</span>
                  <span
                    className={`font-mono text-xs ${intensityColor(editIntensity)}`}
                  >
                    {editIntensity}/10
                  </span>
                </span>
                <input
                  type="range"
                  min="1"
                  max="10"
                  step="1"
                  value={editIntensity}
                  disabled={isSavingEdit}
                  className="h-2 w-full cursor-pointer accent-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setEditIntensity(Number(e.target.value))
                  }
                />
              </label>
            </div>

            <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
              Scene context
              <textarea
                rows={3}
                value={editContext}
                disabled={isSavingEdit}
                className="resize-y rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                placeholder="Additional context..."
                onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                  setEditContext(e.target.value)
                }
              />
            </label>

            <div className="flex justify-end gap-3">
              <button
                type="button"
                disabled={isSavingEdit}
                className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => setIsEditing(false)}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={
                  isSavingEdit ||
                  !editLocation.trim() ||
                  !editActivity.trim()
                }
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
              >
                {isSavingEdit ? "Saving..." : "Save"}
              </button>
            </div>
          </form>
        ) : (
          <div className="flex flex-col gap-3 rounded-lg border border-gray-700 bg-gray-900/60 p-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm text-gray-300">
                <span className="font-medium text-gray-200">Location:</span>{" "}
                {scene.location}
              </span>
              <span className="text-sm text-gray-300">
                <span className="font-medium text-gray-200">Activity:</span>{" "}
                {scene.activity}
              </span>
              <span className="inline-flex items-center rounded-full border border-indigo-700 bg-indigo-900/40 px-2 py-0.5 text-xs font-medium text-indigo-200">
                {scene.tone || "neutral"}
              </span>
              <span
                className={`text-sm font-mono font-semibold ${intensityColor(scene.intensity)}`}
              >
                Intensity: {scene.intensity}/10
              </span>
              {scene.is_paused ? (
                <span className="inline-flex items-center rounded-full border border-yellow-700 bg-yellow-900/40 px-2 py-0.5 text-xs font-medium text-yellow-200">
                  Paused
                </span>
              ) : null}
            </div>
          </div>
        )}

        {/* Pause / Resume / End / Edit buttons */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={isSavingEdit || !!scene.ended_at}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:opacity-50 ${
              scene.is_paused
                ? "bg-emerald-600 text-white hover:bg-emerald-500"
                : "bg-yellow-600 text-white hover:bg-yellow-500"
            }`}
            onClick={handlePauseResume}
          >
            {scene.is_paused ? "Resume" : "Pause"}
          </button>

          {!isEditing ? (
            <button
              type="button"
              disabled={!!scene.ended_at}
              className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={openEditor}
            >
              Edit
            </button>
          ) : null}

          {showEndConfirm ? (
            <div className="flex items-center gap-2 rounded-lg border border-red-700 bg-red-950/30 px-3 py-2 text-sm">
              <span className="text-red-200">End this scene?</span>
              <button
                type="button"
                disabled={isEnding}
                className="rounded bg-red-700 px-2 py-0.5 text-xs font-medium text-white transition hover:bg-red-600 focus:outline-none focus:ring-2 focus:ring-red-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={handleEndScene}
              >
                {isEnding ? "..." : "Yes"}
              </button>
              <button
                type="button"
                disabled={isEnding}
                className="rounded border border-gray-600 px-2 py-0.5 text-xs text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => setShowEndConfirm(false)}
              >
                No
              </button>
            </div>
          ) : (
            <button
              type="button"
              disabled={!!scene.ended_at}
              className="rounded-lg border border-red-700 px-4 py-2 text-sm font-medium text-red-300 transition hover:border-red-500 hover:bg-red-950/30 hover:text-red-200 focus:outline-none focus:ring-2 focus:ring-red-500/60 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => setShowEndConfirm(true)}
            >
              End Scene
            </button>
          )}
        </div>

        {/* Persona Section */}
        <div className="flex flex-col gap-3 rounded-lg border border-gray-700 bg-gray-900/60 p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-200">Persona</h3>
            <button
              type="button"
              className="rounded-lg border border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:border-indigo-500 hover:bg-gray-700 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
              onClick={() => {
                setPersonaName("");
                setPersonaDescription("");
                setPersonaTraits("");
                setPersonaVoice("");
                setPersonaBackstory("");
                setPersonaGoals("");
                setPersonaConstraints("");
                setPersonaError(null);
                setShowPersonaModal(true);
              }}
            >
              Set Persona
            </button>
          </div>

          {systemPrompt ? (
            <div>
              <button
                type="button"
                className="text-xs text-indigo-400 transition hover:text-indigo-300 focus:outline-none"
                onClick={() => setShowPromptPreview(!showPromptPreview)}
              >
                {showPromptPreview
                  ? "Hide system prompt preview"
                  : "Show system prompt preview"}
              </button>

              {showPromptPreview ? (
                <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap break-all rounded-lg border border-gray-700 bg-gray-950 p-3 text-xs leading-6 text-gray-300">
                  {systemPrompt}
                </pre>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-gray-500">No persona set yet.</p>
          )}
        </div>

        {/* Memory Classification */}
        <div className="flex flex-col gap-3 rounded-lg border border-gray-700 bg-gray-900/60 p-4">
          <h3 className="text-sm font-semibold text-gray-200">
            Memory Classification Test
          </h3>

          <form
            className="flex flex-col gap-2 sm:flex-row"
            onSubmit={handleClassify}
          >
            <input
              type="text"
              value={classifyText}
              disabled={isClassifying}
              className="min-w-0 flex-1 rounded-lg border border-gray-600 bg-gray-950 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
              placeholder="Enter text to classify..."
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setClassifyText(e.target.value)
              }
            />
            <button
              type="submit"
              disabled={isClassifying || !classifyText.trim()}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
            >
              {isClassifying ? "..." : "Classify"}
            </button>
          </form>

          {classifyError ? (
            <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
              {classifyError}
              <button
                type="button"
                className="ml-2 underline hover:no-underline"
                onClick={() => setClassifyError(null)}
              >
                Dismiss
              </button>
            </div>
          ) : null}

          {classifyResult ? (
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-300">Result:</span>
              <span
                className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${classificationBadgeColor(classifyResult.classification)}`}
              >
                {classifyResult.classification}
              </span>
            </div>
          ) : null}
        </div>
      </div>

      {/* Persona Modal */}
      {showPersonaModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-lg rounded-lg border border-gray-700 bg-gray-800 p-6 text-gray-100 shadow-xl max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold">Set Persona</h3>

            {personaError ? (
              <div className="mt-3 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
                {personaError}
              </div>
            ) : null}

            <form
              className="mt-4 flex flex-col gap-4"
              onSubmit={handleSavePersona}
            >
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Name
                <input
                  type="text"
                  value={personaName}
                  disabled={isSavingPersona}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="Character name"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setPersonaName(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Description
                <textarea
                  rows={3}
                  value={personaDescription}
                  disabled={isSavingPersona}
                  className="resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="Describe the character"
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                    setPersonaDescription(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Traits (comma-separated)
                <input
                  type="text"
                  value={personaTraits}
                  disabled={isSavingPersona}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="brave, witty, stubborn"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setPersonaTraits(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Voice style
                <input
                  type="text"
                  value={personaVoice}
                  disabled={isSavingPersona}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="Accent, speech patterns, etc."
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setPersonaVoice(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Backstory (optional)
                <textarea
                  rows={3}
                  value={personaBackstory}
                  disabled={isSavingPersona}
                  className="resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="Character background"
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                    setPersonaBackstory(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Scene goals (one per line)
                <textarea
                  rows={3}
                  value={personaGoals}
                  disabled={isSavingPersona}
                  className="resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder={"Find the hidden artifact\nProtect the village"}
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                    setPersonaGoals(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Constraints (one per line)
                <textarea
                  rows={3}
                  value={personaConstraints}
                  disabled={isSavingPersona}
                  className="resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder={"Cannot speak about the past\nMust stay in character"}
                  onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                    setPersonaConstraints(e.target.value)
                  }
                />
              </label>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  disabled={isSavingPersona}
                  className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => {
                    setShowPersonaModal(false);
                    setPersonaError(null);
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={
                    isSavingPersona ||
                    !personaName.trim() ||
                    !personaDescription.trim()
                  }
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
                >
                  {isSavingPersona ? "Saving..." : "Save Persona"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export default SceneControls;