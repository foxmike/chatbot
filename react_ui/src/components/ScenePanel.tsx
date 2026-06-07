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

export interface ScenePanelProps {
  roomId: string | null;
  roomType: RoomType | null;
}

interface SceneTemplate {
  name: string;
  location: string;
  activity: string;
  tone: string;
  intensity: number;
  icon: string;
  description: string;
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

const SCENE_TEMPLATES: SceneTemplate[] = [
  {
    name: "Cozy Coffee Shop",
    location: "A warm, rustic coffee shop in a quiet neighborhood",
    activity: "Chatting over warm drinks while rain gently taps on the window",
    tone: "neutral",
    intensity: 3,
    icon: "☕",
    description: "Relaxed atmosphere, soft jazz playing, and warm ambient light."
  },
  {
    name: "Cyberpunk Alleyway",
    location: "A rain-slicked alley in Neo-Seoul under towering hologram signs",
    activity: "Waiting for a contact to deliver a decrypted data shard",
    tone: "tense",
    intensity: 8,
    icon: "🚀",
    description: "Futuristic, gritty, high-stakes atmosphere with hovering patrol drones."
  },
  {
    name: "Fantasy Tavern",
    location: "The Drunken Dragon Inn, sitting near a crackling stone fireplace",
    activity: "Planning an upcoming quest to the Whispering Woods over flagons of mead",
    tone: "playful",
    intensity: 5,
    icon: "🎭",
    description: "Rustic tavern warmth, lively ambient chatter, and scent of roasted meats."
  },
  {
    name: "Enchanted Grove",
    location: "A clearing in an ancient forest illuminated by glowing mushrooms",
    activity: "Sharing secrets and tracing the patterns of magical energy in the air",
    tone: "mysterious",
    intensity: 4,
    icon: "✨",
    description: "Dreamy fantasy setting, soft ambient whispers, and floating moonbeams."
  },
];

function getErrorDetail(error: unknown): string {
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

function toneBadgeColor(tone: string): string {
  switch (tone) {
    case "neutral":
      return "border-gray-700 bg-gray-800 text-gray-300";
    case "playful":
      return "border-pink-500/30 bg-pink-500/10 text-pink-300";
    case "serious":
      return "border-blue-500/30 bg-blue-500/10 text-blue-300";
    case "tense":
      return "border-orange-500/30 bg-orange-500/10 text-orange-300";
    case "romantic":
      return "border-red-500/30 bg-red-500/10 text-red-300";
    case "mysterious":
      return "border-purple-500/30 bg-purple-500/10 text-purple-300";
    case "comedic":
      return "border-yellow-500/30 bg-yellow-500/10 text-yellow-300";
    case "dramatic":
      return "border-indigo-500/30 bg-indigo-500/10 text-indigo-300";
    default:
      return "border-gray-700 bg-gray-800 text-gray-300";
  }
}

function intensityColor(value: number): string {
  if (value <= 3) return "text-emerald-400";
  if (value <= 6) return "text-yellow-400";
  return "text-rose-400";
}

function classificationBadgeColor(classification: string): string {
  switch (classification) {
    case "scene_only":
      return "border-blue-500/30 bg-blue-500/10 text-blue-300";
    case "durable":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
    case "both":
      return "border-purple-500/30 bg-purple-500/10 text-purple-300";
    default:
      return "border-gray-700 bg-gray-800 text-gray-300";
  }
}

export function ScenePanel({ roomId, roomType }: ScenePanelProps) {
  // Active scene state
  const [scene, setScene] = useState<SceneData | null>(null);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Form parameters for new scene
  const [location, setLocation] = useState("");
  const [activity, setActivity] = useState("");
  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // End scene confirmation
  const [showEndConfirm, setShowEndConfirm] = useState(false);
  const [isEnding, setIsEnding] = useState(false);

  // Inline editing
  const [isEditing, setIsEditing] = useState(false);
  const [editLocation, setEditLocation] = useState("");
  const [editActivity, setEditActivity] = useState("");
  const [editTone, setEditTone] = useState("neutral");
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
  const [classifyResult, setClassifyResult] = useState<ClassificationResponse | null>(null);
  const [isClassifying, setIsClassifying] = useState(false);
  const [classifyError, setClassifyError] = useState<string | null>(null);

  // General action error
  const [actionError, setActionError] = useState<string | null>(null);

  const loadScene = async () => {
    if (!roomId) return;

    setIsFetching(true);
    setError(null);

    try {
      const result = await getActiveScene(roomId);
      setScene(result);
    } catch (err) {
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
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, roomType]);

  const handleStartScene = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!roomId || !location.trim() || !activity.trim()) return;

    setIsStarting(true);
    setStartError(null);

    try {
      const result = await createScene(roomId, {
        room_id: roomId,
        location: location.trim(),
        activity: activity.trim(),
      });
      setScene(result);
      setLocation("");
      setActivity("");
    } catch (err) {
      console.error("Failed to start scene", err);
      setStartError(getErrorDetail(err));
    } finally {
      setIsStarting(false);
    }
  };

  const handleTogglePause = async () => {
    if (!scene) return;

    setIsSavingEdit(true);
    setActionError(null);

    try {
      const result = scene.is_paused
        ? await resumeScene(scene.scene_id)
        : await pauseScene(scene.scene_id);
      setScene(result);
    } catch (err) {
      console.error("Failed to toggle pause status", err);
      setActionError(getErrorDetail(err));
    } finally {
      setIsSavingEdit(false);
    }
  };

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

  const openEditor = () => {
    if (!scene) return;
    setEditLocation(scene.location);
    setEditActivity(scene.activity);
    setEditTone(scene.tone);
    setEditIntensity(scene.intensity);
    setEditContext(scene.scene_context || "");
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
        scene_context: editContext.trim(),
      });
      setScene(result);
      setIsEditing(false);
    } catch (err) {
      console.error("Failed to update scene details", err);
      setActionError(getErrorDetail(err));
    } finally {
      setIsSavingEdit(false);
    }
  };

  const handleSelectTemplate = (template: SceneTemplate) => {
    setLocation(template.location);
    setActivity(template.activity);
  };

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
      console.error("Failed to set scene persona", err);
      setPersonaError(getErrorDetail(err));
    } finally {
      setIsSavingPersona(false);
    }
  };

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
      console.error("Failed to classify scene memory", err);
      setClassifyError(getErrorDetail(err));
    } finally {
      setIsClassifying(false);
    }
  };

  if (roomType !== "roleplay_scene") {
    return null;
  }

  if (!roomId) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-6 text-center">
        <span className="text-4xl">🎭</span>
        <h3 className="mt-4 text-lg font-semibold text-gray-200">No Room Selected</h3>
        <p className="mt-2 text-sm text-gray-500">Select or create a roleplay room first.</p>
      </div>
    );
  }

  if (isFetching) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
          <span className="text-sm text-gray-400">Loading scene configurations...</span>
        </div>
      </div>
    );
  }

  // Active scene ended state (i.e. we have active scene but ended_at is set, which is functionally no active scene)
  const isSceneActive = scene && !scene.ended_at;

  return (
    <div className="flex flex-col gap-6 p-6">
      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-950/20 p-4 text-sm text-red-400 backdrop-blur-md">
          {error}
        </div>
      )}

      {/* ── NO ACTIVE SCENE ── */}
      {!isSceneActive ? (
        <div className="flex flex-col gap-6">
          <div className="rounded-2xl border border-gray-800/80 bg-gray-950/60 p-6 shadow-xl backdrop-blur-md">
            <h2 className="text-xl font-bold bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
              Create a Roleplay Scene
            </h2>
            <p className="mt-2 text-sm text-gray-400">
              Establish the starting context, location, and activity for this roleplay room.
            </p>

            {/* Quick Template Selector */}
            <div className="mt-6">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                Select a Template Scene
              </h3>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                {SCENE_TEMPLATES.map((tmpl) => (
                  <button
                    key={tmpl.name}
                    type="button"
                    onClick={() => handleSelectTemplate(tmpl)}
                    className="flex flex-col items-start rounded-xl border border-gray-800 bg-gray-900/40 p-4 text-left transition-all duration-200 hover:border-indigo-500/50 hover:bg-gray-900/80 hover:shadow-lg hover:shadow-indigo-500/5 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                  >
                    <span className="flex items-center gap-2 text-sm font-semibold text-gray-200">
                      <span>{tmpl.icon}</span>
                      <span>{tmpl.name}</span>
                    </span>
                    <span className="mt-1 text-xs text-gray-500 line-clamp-2">
                      {tmpl.description}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Custom Scene Form */}
            <form onSubmit={handleStartScene} className="mt-6 flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <label htmlFor="scene-location" className="text-sm font-semibold text-gray-300">
                  Location
                </label>
                <input
                  id="scene-location"
                  type="text"
                  placeholder="e.g., A busy street in Tokyo, a cozy cabin"
                  value={location}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setLocation(e.target.value)}
                  className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                  required
                />
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="scene-activity" className="text-sm font-semibold text-gray-300">
                  Activity
                </label>
                <input
                  id="scene-activity"
                  type="text"
                  placeholder="e.g., Exchanging secretive whispers, sipping tea together"
                  value={activity}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setActivity(e.target.value)}
                  className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                  required
                />
              </div>

              {startError && <p className="text-sm text-rose-400">{startError}</p>}

              <button
                type="submit"
                disabled={isStarting || !location.trim() || !activity.trim()}
                className="mt-2 relative overflow-hidden rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 px-5 py-3 text-sm font-semibold text-white shadow-lg transition-all duration-200 hover:from-indigo-400 hover:to-purple-500 hover:shadow-indigo-500/20 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 active:scale-[0.98] disabled:scale-100 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isStarting ? "Creating Scene..." : "Begin Scene 🎭"}
              </button>
            </form>
          </div>
        </div>
      ) : (
        /* ── ACTIVE SCENE CONTROL PANEL ── */
        <div className="flex flex-col gap-6">
          {actionError && (
            <div className="rounded-xl border border-red-500/20 bg-red-950/20 p-4 text-sm text-red-400 flex items-center justify-between backdrop-blur-md">
              <span>{actionError}</span>
              <button
                type="button"
                onClick={() => setActionError(null)}
                className="text-red-400 hover:text-red-300 font-medium underline text-xs"
              >
                Dismiss
              </button>
            </div>
          )}

          {/* Active Scene Summary */}
          <div className="rounded-2xl border border-gray-800 bg-gray-950/60 p-6 shadow-xl backdrop-blur-md">
            <div className="flex items-start justify-between">
              <div>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-500/10 px-2.5 py-1 text-xs font-semibold text-indigo-400 border border-indigo-500/20">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                  </span>
                  Active Roleplay Scene
                </span>
                <h2 className="mt-3 text-xl font-bold text-gray-100">
                  {scene?.location}
                </h2>
                <p className="mt-1 text-sm text-gray-400">{scene?.activity}</p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={openEditor}
                  className="rounded-xl border border-gray-800 bg-gray-900/40 p-2.5 text-gray-400 hover:border-gray-700 hover:bg-gray-800 hover:text-white transition-all duration-200"
                  title="Edit Scene"
                >
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                </button>
              </div>
            </div>

            <div className="mt-6 flex flex-wrap gap-4 border-t border-gray-900 pt-5">
              <div className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Tone</span>
                <span className={`inline-flex items-center rounded-lg border px-2.5 py-0.5 text-xs font-semibold capitalize ${toneBadgeColor(scene?.tone || "neutral")}`}>
                  {scene?.tone || "neutral"}
                </span>
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Intensity</span>
                <span className={`text-sm font-mono font-bold ${intensityColor(scene?.intensity || 5)}`}>
                  {scene?.intensity || 5}/10
                </span>
              </div>

              {scene?.is_paused && (
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</span>
                  <span className="inline-flex items-center rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-2.5 py-0.5 text-xs font-semibold text-yellow-300">
                    Paused
                  </span>
                </div>
              )}
            </div>

            {scene?.scene_context && (
              <div className="mt-5 rounded-xl border border-gray-900 bg-gray-900/20 p-4 text-sm text-gray-400">
                <span className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                  Context
                </span>
                {scene.scene_context}
              </div>
            )}

            {/* Quick Actions (Pause/Resume/End) */}
            <div className="mt-6 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={handleTogglePause}
                disabled={isSavingEdit}
                className={`flex-1 flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-semibold transition-all duration-200 disabled:opacity-50 ${
                  scene?.is_paused
                    ? "bg-emerald-600 text-white hover:bg-emerald-500"
                    : "bg-amber-600 text-white hover:bg-amber-500"
                }`}
              >
                {scene?.is_paused ? "▶ Resume Scene" : "⏸ Pause Scene"}
              </button>

              <button
                type="button"
                onClick={() => setShowEndConfirm(true)}
                className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-rose-600/10 border border-rose-500/30 px-5 py-3 text-sm font-semibold text-rose-400 transition-all duration-200 hover:bg-rose-600 hover:text-white"
              >
                ⏹ End Scene
              </button>
            </div>
          </div>

          {/* Inline Edit Form */}
          {isEditing && (
            <div className="rounded-2xl border border-gray-800 bg-gray-950/60 p-6 shadow-xl backdrop-blur-md">
              <form onSubmit={handleSaveEdit} className="flex flex-col gap-4">
                <h3 className="text-lg font-bold text-gray-200">Modify Current Scene</h3>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="flex flex-col gap-2">
                    <label htmlFor="edit-location" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                      Location
                    </label>
                    <input
                      id="edit-location"
                      type="text"
                      value={editLocation}
                      disabled={isSavingEdit}
                      className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setEditLocation(e.target.value)}
                      required
                    />
                  </div>

                  <div className="flex flex-col gap-2">
                    <label htmlFor="edit-activity" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                      Activity
                    </label>
                    <input
                      id="edit-activity"
                      type="text"
                      value={editActivity}
                      disabled={isSavingEdit}
                      className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setEditActivity(e.target.value)}
                      required
                    />
                  </div>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="flex flex-col gap-2">
                    <label htmlFor="edit-tone" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                      Tone
                    </label>
                    <select
                      id="edit-tone"
                      value={editTone}
                      disabled={isSavingEdit}
                      className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                      onChange={(e: ChangeEvent<HTMLSelectElement>) => setEditTone(e.target.value)}
                    >
                      {TONE_OPTIONS.map((tone) => (
                        <option key={tone} value={tone} className="bg-gray-950 text-gray-100">
                          {tone}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="flex flex-col gap-2">
                    <span className="flex items-center justify-between text-xs font-semibold text-gray-300 uppercase tracking-wider">
                      <span>Intensity</span>
                      <span className={`font-mono text-sm font-bold ${intensityColor(editIntensity)}`}>
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
                      className="h-2 w-full cursor-pointer accent-indigo-500 mt-3 disabled:opacity-50"
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setEditIntensity(Number(e.target.value))}
                    />
                  </div>
                </div>

                <div className="flex flex-col gap-2">
                  <label htmlFor="edit-context" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                    Scene Context Notes
                  </label>
                  <textarea
                    id="edit-context"
                    rows={3}
                    value={editContext}
                    disabled={isSavingEdit}
                    placeholder="Enter any additional backstory, details, or events to shape the companion's behavior..."
                    className="resize-y w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                    onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setEditContext(e.target.value)}
                  />
                </div>

                <div className="flex justify-end gap-3 mt-2">
                  <button
                    type="button"
                    disabled={isSavingEdit}
                    onClick={() => setIsEditing(false)}
                    className="rounded-xl border border-gray-800 bg-gray-900/40 px-5 py-3 text-sm font-semibold text-gray-300 transition-all duration-200 hover:bg-gray-800/80 hover:text-white"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={isSavingEdit || !editLocation.trim() || !editActivity.trim()}
                    className="rounded-xl bg-indigo-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-50"
                  >
                    {isSavingEdit ? "Saving..." : "Save Changes"}
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* End Scene Confirmation */}
          {showEndConfirm && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
              <div className="w-full max-w-md rounded-2xl border border-gray-800 bg-gray-950 p-6 shadow-2xl animate-in fade-in zoom-in-95 duration-200">
                <h3 className="text-lg font-bold text-gray-100">End this scene?</h3>
                <p className="mt-2 text-sm text-gray-400">
                  This will permanently close the current scene. You will not be able to resume it. A new scene can be started at any time.
                </p>
                <div className="mt-6 flex justify-end gap-3">
                  <button
                    type="button"
                    disabled={isEnding}
                    onClick={() => setShowEndConfirm(false)}
                    className="rounded-xl border border-gray-800 bg-gray-900/40 px-4 py-2 text-sm font-semibold text-gray-300 transition hover:bg-gray-800 hover:text-white"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={isEnding}
                    onClick={handleEndScene}
                    className="rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-500 disabled:opacity-50"
                  >
                    {isEnding ? "Ending..." : "End Scene"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Persona Configuration section */}
          <div className="rounded-2xl border border-gray-800 bg-gray-950/60 p-6 shadow-xl backdrop-blur-md">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-gray-100">Roleplay Persona</h3>
                <p className="mt-1 text-xs text-gray-400">Customize the traits and behavior of the companion for this scene.</p>
              </div>
              <button
                type="button"
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
                className="rounded-xl border border-gray-800 bg-gray-900/40 px-4 py-2.5 text-xs font-semibold text-gray-200 hover:border-gray-700 hover:bg-gray-800 transition-all duration-200"
              >
                Set Persona 🎭
              </button>
            </div>

            {systemPrompt && (
              <div className="mt-5">
                <button
                  type="button"
                  onClick={() => setShowPromptPreview(!showPromptPreview)}
                  className="flex w-full items-center justify-between rounded-xl bg-gray-900/30 border border-gray-900 px-4 py-3 text-left text-xs font-semibold text-indigo-400 hover:bg-gray-900/50 transition-all duration-200"
                >
                  <span>{showPromptPreview ? "Hide Prompt Preview" : "View Generated Prompt"}</span>
                  <svg
                    className={`h-4 w-4 transform transition-transform duration-200 ${showPromptPreview ? "rotate-180" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>

                {showPromptPreview && (
                  <div className="mt-3 max-h-48 overflow-y-auto rounded-xl bg-gray-950 border border-gray-900 p-4 font-mono text-xs text-gray-400 whitespace-pre-wrap leading-5">
                    {systemPrompt}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Persona Configuration Modal */}
          {showPersonaModal && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 overflow-y-auto">
              <div className="my-8 w-full max-w-lg rounded-2xl border border-gray-800 bg-gray-950 p-6 shadow-2xl animate-in fade-in zoom-in-95 duration-200">
                <h3 className="text-lg font-bold text-gray-100">Set Scene Persona</h3>
                <p className="mt-1 text-xs text-gray-400">Configure character settings specifically injected into this roleplay scene.</p>

                <form onSubmit={handleSavePersona} className="mt-6 flex flex-col gap-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="flex flex-col gap-1.5">
                      <label htmlFor="persona-name" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                        Name
                      </label>
                      <input
                        id="persona-name"
                        type="text"
                        value={personaName}
                        onChange={(e: ChangeEvent<HTMLInputElement>) => setPersonaName(e.target.value)}
                        placeholder="e.g., Kaelen"
                        className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-2.5 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                        required
                      />
                    </div>

                    <div className="flex flex-col gap-1.5">
                      <label htmlFor="persona-voice" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                        Voice Style
                      </label>
                      <input
                        id="persona-voice"
                        type="text"
                        value={personaVoice}
                        onChange={(e: ChangeEvent<HTMLInputElement>) => setPersonaVoice(e.target.value)}
                        placeholder="e.g., Whispering, soft, mysterious"
                        className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-2.5 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                        required
                      />
                    </div>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="persona-description" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                      Description
                    </label>
                    <input
                      id="persona-description"
                      type="text"
                      value={personaDescription}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setPersonaDescription(e.target.value)}
                      placeholder="e.g., A weary mage searching for ancient lost secrets"
                      className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-2.5 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                      required
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="persona-traits" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                      Traits (comma-separated)
                    </label>
                    <input
                      id="persona-traits"
                      type="text"
                      value={personaTraits}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setPersonaTraits(e.target.value)}
                      placeholder="e.g., quiet, analytical, cautious, loyal"
                      className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-2.5 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                      required
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="persona-backstory" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                      Backstory
                    </label>
                    <textarea
                      id="persona-backstory"
                      rows={2}
                      value={personaBackstory}
                      onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPersonaBackstory(e.target.value)}
                      placeholder="Optional backstory details..."
                      className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-2.5 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                    />
                  </div>

                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="flex flex-col gap-1.5">
                      <label htmlFor="persona-goals" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                        Goals (one per line)
                      </label>
                      <textarea
                        id="persona-goals"
                        rows={2}
                        value={personaGoals}
                        onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPersonaGoals(e.target.value)}
                        placeholder="Optional scene goals..."
                        className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-2.5 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                      />
                    </div>

                    <div className="flex flex-col gap-1.5">
                      <label htmlFor="persona-constraints" className="text-xs font-semibold text-gray-300 uppercase tracking-wider">
                        Constraints (one per line)
                      </label>
                      <textarea
                        id="persona-constraints"
                        rows={2}
                        value={personaConstraints}
                        onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setPersonaConstraints(e.target.value)}
                        placeholder="Optional behavior constraints..."
                        className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-2.5 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                      />
                    </div>
                  </div>

                  {personaError && <p className="text-sm text-rose-400">{personaError}</p>}

                  <div className="mt-4 flex justify-end gap-3">
                    <button
                      type="button"
                      disabled={isSavingPersona}
                      onClick={() => setShowPersonaModal(false)}
                      className="rounded-xl border border-gray-800 bg-gray-900/40 px-4 py-2 text-sm font-semibold text-gray-300 transition hover:bg-gray-800 hover:text-white"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={
                        isSavingPersona ||
                        !personaName.trim() ||
                        !personaDescription.trim() ||
                        !personaTraits.trim() ||
                        !personaVoice.trim()
                      }
                      className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-50"
                    >
                      {isSavingPersona ? "Saving..." : "Save Persona"}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

          {/* Memory Classification Test */}
          <div className="rounded-2xl border border-gray-800 bg-gray-950/60 p-6 shadow-xl backdrop-blur-md">
            <h3 className="text-base font-bold text-gray-100">Memory Classification Test</h3>
            <p className="mt-1 text-xs text-gray-400">Classify whether information should persist long-term (durable) or only inside this scene.</p>

            <form onSubmit={handleClassify} className="mt-4 flex gap-3">
              <input
                type="text"
                value={classifyText}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setClassifyText(e.target.value)}
                placeholder="Type a statement to classify..."
                className="flex-1 rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-2.5 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                required
              />
              <button
                type="submit"
                disabled={isClassifying || !classifyText.trim()}
                className="rounded-xl bg-indigo-600 px-4 py-2.5 text-xs font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-50"
              >
                {isClassifying ? "Testing..." : "Classify"}
              </button>
            </form>

            {classifyError && <p className="mt-2 text-xs text-rose-400">{classifyError}</p>}

            {classifyResult && (
              <div className="mt-4 flex items-center gap-2">
                <span className="text-xs text-gray-400">Classification Result:</span>
                <span className={`inline-flex items-center rounded-lg border px-2.5 py-0.5 text-xs font-semibold capitalize ${classificationBadgeColor(classifyResult.classification)}`}>
                  {classifyResult.classification.replace("_", " ")}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default ScenePanel;
