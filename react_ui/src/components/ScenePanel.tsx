import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  getActiveScene,
  createScene,
  updateScene,
  listCompanions,
  createRoom,
} from "../api/client";
import type {
  SceneData,
  Companion,
  RoomData,
} from "../api/client";
import type { ApiError } from "../types";

export interface ScenePanelProps {
  userId: string;
  activeRoom: RoomData | null;
  onBeginScene: (room: RoomData) => void;
  onRoomUpdated?: (room: RoomData) => void;
  onCancel: () => void;
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

function intensityColor(value: number): string {
  if (value <= 3) return "text-emerald-400";
  if (value <= 6) return "text-yellow-400";
  return "text-rose-400";
}

export function ScenePanel({
  userId,
  activeRoom,
  onBeginScene,
  onRoomUpdated,
  onCancel,
}: ScenePanelProps) {
  // Active scene state
  const [scene, setScene] = useState<SceneData | null>(null);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Companion list
  const [companions, setCompanions] = useState<Companion[]>([]);

  // Form parameters
  const [roomName, setRoomName] = useState("");
  const [companionId, setCompanionId] = useState("");
  const [location, setLocation] = useState("");
  const [activity, setActivity] = useState("");
  const [tone, setTone] = useState<string>("neutral");
  const [intensity, setIntensity] = useState<number>(5);
  const [sceneContext, setSceneContext] = useState("");

  const [isStarting, setIsStarting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isStartingNewScene, setIsStartingNewScene] = useState(false);

  // Validation errors
  const [roomNameError, setRoomNameError] = useState<string | null>(null);

  const isEditMode = activeRoom !== null && activeRoom.room_type === "roleplay_scene";

  // Load companions on mount
  useEffect(() => {
    const loadCompanions = async () => {
      try {
        const list = await listCompanions();
        setCompanions(list);
      } catch (err) {
        console.error("Failed to load companions", err);
      }
    };
    loadCompanions();
  }, []);

  // Sync form states with active room
  useEffect(() => {
    const loadActiveScene = async () => {
      if (activeRoom && activeRoom.room_type === "roleplay_scene") {
        setIsFetching(true);
        setError(null);
        setRoomNameError(null);
        try {
          const activeScene = await getActiveScene(activeRoom.room_id);
          setScene(activeScene);
          setRoomName(activeRoom.display_name);
          setCompanionId(activeRoom.companion_id || "");
          setLocation(activeScene.location || "");
          setActivity(activeScene.activity || "");
          setTone(activeScene.tone || "neutral");
          setIntensity(activeScene.intensity || 5);
          setSceneContext(activeScene.scene_context || "");
        } catch (err) {
          const apiErr = err as ApiError;
          if (apiErr?.status_code === 404) {
            setScene(null);
            setRoomName(activeRoom.display_name);
            setCompanionId(activeRoom.companion_id || "");
            setLocation("");
            setActivity("");
            setTone("neutral");
            setIntensity(5);
            setSceneContext("");
          } else {
            console.error("Failed to load active scene", err);
            setError(getErrorDetail(err));
          }
        } finally {
          setIsFetching(false);
        }
      } else {
        setScene(null);
        setIsFetching(false);
        setRoomNameError(null);
        setRoomName("");
        if (companions.length > 0) {
          setCompanionId(companions[0].companion_id);
        } else {
          setCompanionId("");
        }
        setLocation("");
        setActivity("");
        setTone("neutral");
        setIntensity(5);
        setSceneContext("");
      }
    };

    loadActiveScene();
  }, [activeRoom, companions]);

  const handleRoomNameChange = (e: ChangeEvent<HTMLInputElement>) => {
    setRoomName(e.target.value);
    if (roomNameError) {
      setRoomNameError(null);
    }
  };

  const handleSelectTemplate = (template: SceneTemplate) => {
    setLocation(template.location);
    setActivity(template.activity);
    setTone(template.tone);
    setIntensity(template.intensity);
    if (!isEditMode && !roomName.trim()) {
      setRoomName(template.name);
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!location.trim() || !activity.trim() || !roomName.trim() || !companionId) return;

    if (isEditMode && activeRoom) {
      setIsSaving(true);
      setError(null);
      try {
        let updatedScene: SceneData;
        if (!scene) {
          const newScene = await createScene(activeRoom.room_id, {
            room_id: activeRoom.room_id,
            location: location.trim(),
            activity: activity.trim(),
          });
          updatedScene = await updateScene(newScene.scene_id, {
            tone,
            intensity,
            scene_context: sceneContext.trim(),
          });
        } else {
          updatedScene = await updateScene(scene.scene_id, {
            location: location.trim(),
            activity: activity.trim(),
            tone,
            intensity,
            scene_context: sceneContext.trim(),
          });
        }
        setScene(updatedScene);
        if (onRoomUpdated) {
          onRoomUpdated(activeRoom);
        }
      } catch (err) {
        console.error("Failed to save scene changes", err);
        setError(getErrorDetail(err));
      } finally {
        setIsSaving(false);
      }
    } else {
      setIsStarting(true);
      setError(null);
      try {
        const room = await createRoom({
          user_id: userId,
          room_type: "roleplay_scene",
          display_name: roomName.trim(),
          companion_id: companionId,
        });

        const newScene = await createScene(room.room_id, {
          room_id: room.room_id,
          location: location.trim(),
          activity: activity.trim(),
        });

        await updateScene(newScene.scene_id, {
          tone,
          intensity,
          scene_context: sceneContext.trim(),
        });

        onBeginScene(room);
      } catch (err) {
        console.error("Failed to create roleplay room/scene", err);
        setError(getErrorDetail(err));
      } finally {
        setIsStarting(false);
      }
    }
  };

  const handleBeginNewSceneInEditMode = async () => {
    if (!activeRoom) return;
    if (!roomName.trim() || !companionId || !location.trim() || !activity.trim()) return;

    // Validate Room Name has been changed
    if (roomName.trim() === activeRoom.display_name) {
      setRoomNameError("Change the room name to create a new roleplay.");
      return;
    }

    setIsStartingNewScene(true);
    setError(null);
    try {
      const room = await createRoom({
        user_id: userId,
        room_type: "roleplay_scene",
        display_name: roomName.trim(),
        companion_id: companionId,
      });

      const newScene = await createScene(room.room_id, {
        room_id: room.room_id,
        location: location.trim(),
        activity: activity.trim(),
      });

      await updateScene(newScene.scene_id, {
        tone,
        intensity,
        scene_context: sceneContext.trim(),
      });

      onBeginScene(room);
    } catch (err) {
      console.error("Failed to clone/create roleplay room", err);
      setError(getErrorDetail(err));
    } finally {
      setIsStartingNewScene(false);
    }
  };

  if (isFetching) {
    return (
      <div className="flex h-full items-center justify-center p-6 bg-gray-900">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
          <span className="text-sm text-gray-400">Loading scene configurations...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6 h-full overflow-y-auto bg-gray-900">
      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-950/20 p-4 text-sm text-red-400 backdrop-blur-md">
          {error}
        </div>
      )}

      {/* ── CONSOLIDATED ROLEPLAY FORM ── */}
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="rounded-2xl border border-gray-800/80 bg-gray-950/60 p-6 shadow-xl backdrop-blur-md flex flex-col gap-6">
          <div>
            <h2 className="text-xl font-bold bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
              {isEditMode ? "Edit Roleplay Scene" : "Create a Roleplay Scene"}
            </h2>
            <p className="mt-2 text-sm text-gray-400">
              {isEditMode
                ? "Update the active scene parameters, companion, and context notes."
                : "Establish the companion, room name, template or custom scene context."}
            </p>
          </div>

          {/* Companion Selector & Room Name */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label htmlFor="companion-select" className="text-sm font-semibold text-gray-300">
                Companion
              </label>
              <select
                id="companion-select"
                value={companionId}
                disabled={isStarting || isSaving || isStartingNewScene}
                className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200 disabled:opacity-60 disabled:cursor-not-allowed"
                onChange={(e: ChangeEvent<HTMLSelectElement>) => setCompanionId(e.target.value)}
                required
              >
                <option value="">Select a companion...</option>
                {companions.map((c) => (
                  <option key={c.companion_id} value={c.companion_id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-2">
              <label htmlFor="room-name" className="text-sm font-semibold text-gray-300">
                Room Name
              </label>
              <input
                id="room-name"
                type="text"
                placeholder="e.g., Rainy Coffee Shop"
                value={roomName}
                disabled={isStarting || isSaving || isStartingNewScene}
                className={`w-full rounded-xl border bg-gray-900/50 px-4 py-3 text-sm text-gray-100 placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200 disabled:opacity-60 disabled:cursor-not-allowed ${
                  roomNameError ? "border-red-500 focus:border-red-500" : "border-gray-800 focus:border-indigo-500/80"
                }`}
                onChange={handleRoomNameChange}
                required
              />
              {roomNameError && (
                <span className="text-xs text-red-400 mt-1 font-medium">{roomNameError}</span>
              )}
            </div>
          </div>

          {/* Quick Template Selector */}
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
              Select a Template Scene (Quick-fill)
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

          {/* Location & Activity */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label htmlFor="scene-location" className="text-sm font-semibold text-gray-300">
                Location
              </label>
              <input
                id="scene-location"
                type="text"
                placeholder="e.g., A busy street in Tokyo, a cozy cabin"
                value={location}
                disabled={isStarting || isSaving || isStartingNewScene}
                className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                onChange={(e: ChangeEvent<HTMLInputElement>) => setLocation(e.target.value)}
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
                disabled={isStarting || isSaving || isStartingNewScene}
                className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                onChange={(e: ChangeEvent<HTMLInputElement>) => setActivity(e.target.value)}
                required
              />
            </div>
          </div>

          {/* Tone & Intensity */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label htmlFor="scene-tone" className="text-sm font-semibold text-gray-300">
                Tone
              </label>
              <select
                id="scene-tone"
                value={tone}
                disabled={isStarting || isSaving || isStartingNewScene}
                className="w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm text-gray-100 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
                onChange={(e: ChangeEvent<HTMLSelectElement>) => setTone(e.target.value)}
              >
                {TONE_OPTIONS.map((t) => (
                  <option key={t} value={t} className="bg-gray-950 text-gray-100">
                    {t}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-2">
              <span className="flex items-center justify-between text-sm font-semibold text-gray-300">
                <span>Intensity</span>
                <span className={`font-mono text-sm font-bold ${intensityColor(intensity)}`}>
                  {intensity}/10
                </span>
              </span>
              <input
                type="range"
                min="1"
                max="10"
                step="1"
                value={intensity}
                disabled={isStarting || isSaving || isStartingNewScene}
                className="h-2 w-full cursor-pointer accent-indigo-500 mt-3 disabled:opacity-50"
                onChange={(e: ChangeEvent<HTMLInputElement>) => setIntensity(Number(e.target.value))}
              />
            </div>
          </div>

          {/* Scene Context Notes */}
          <div className="flex flex-col gap-2">
            <label htmlFor="scene-context" className="text-sm font-semibold text-gray-300">
              Scene Context Notes
            </label>
            <textarea
              id="scene-context"
              rows={4}
              value={sceneContext}
              disabled={isStarting || isSaving || isStartingNewScene}
              placeholder="Enter any additional backstory, details, or events to shape the companion's behavior..."
              className="resize-y w-full rounded-xl border border-gray-800 bg-gray-900/50 px-4 py-3 text-sm leading-6 text-gray-100 placeholder:text-gray-500 focus:border-indigo-500/80 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:bg-gray-900 transition-all duration-200"
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setSceneContext(e.target.value)}
            />
          </div>

          {/* Form Actions */}
          <div className="flex justify-end gap-3 mt-2">
            {isEditMode && (
              <>
                <button
                  type="button"
                  disabled={isSaving || isStartingNewScene}
                  onClick={onCancel}
                  className="rounded-xl border border-gray-800 bg-gray-900/40 px-5 py-3 text-sm font-semibold text-gray-300 transition-all duration-200 hover:bg-gray-800/80 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSaving || isStartingNewScene || !location.trim() || !activity.trim() || !roomName.trim() || !companionId}
                  className="rounded-xl bg-indigo-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-50"
                >
                  {isSaving ? "Saving..." : "Save"}
                </button>
                <button
                  type="button"
                  disabled={isSaving || isStartingNewScene || !location.trim() || !activity.trim() || !roomName.trim() || !companionId}
                  onClick={handleBeginNewSceneInEditMode}
                  className="relative overflow-hidden rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 px-5 py-3 text-sm font-semibold text-white shadow-lg transition-all duration-200 hover:from-indigo-400 hover:to-purple-500 hover:shadow-indigo-500/20 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 active:scale-[0.98] disabled:scale-100 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isStartingNewScene ? "Creating Scene..." : "Begin Scene 🎭"}
                </button>
              </>
            )}
            {!isEditMode && (
              <button
                type="submit"
                disabled={isStarting || !location.trim() || !activity.trim() || !roomName.trim() || !companionId}
                className="relative overflow-hidden rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 px-5 py-3 text-sm font-semibold text-white shadow-lg transition-all duration-200 hover:from-indigo-400 hover:to-purple-500 hover:shadow-indigo-500/20 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 active:scale-[0.98] disabled:scale-100 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isStarting ? "Creating Scene..." : "Begin Scene 🎭"}
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}

export default ScenePanel;
