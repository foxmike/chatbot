import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  createRoom,
  deleteRoom,
  listRooms,
} from "../api/client";
import type { RoomData, RoomType } from "../api/client";
import type { ApiError } from "../types";

interface RoomSwitcherProps {
  userId: string;
  activeRoomId: string | null;
  onRoomChange: (roomId: string, roomType: RoomType) => void;
}

const ROOM_TYPE_LABELS: Record<RoomType, string> = {
  one_on_one_chat: "One-on-One Chat",
  roleplay_scene: "Roleplay Scene",
  project: "Project",
  emotional_support: "Emotional Support",
  memory_review: "Memory Review",
};

const ROOM_TYPE_ICONS: Record<RoomType, string> = {
  one_on_one_chat: "\uD83D\uDCAC",
  roleplay_scene: "\uD83C\uDFAD",
  project: "\uD83D\uDCC1",
  emotional_support: "\u2764\uFE0F",
  memory_review: "\uD83E\uDDE0",
};

const ROOM_TYPE_OPTIONS: RoomType[] = [
  "one_on_one_chat",
  "roleplay_scene",
  "project",
  "emotional_support",
  "memory_review",
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

export function RoomSwitcher({
  userId,
  activeRoomId,
  onRoomChange,
}: RoomSwitcherProps) {
  const [rooms, setRooms] = useState<RoomData[]>([]);
  const [isFetching, setIsFetching] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newRoomType, setNewRoomType] = useState<RoomType>("one_on_one_chat");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [newCompanionId, setNewCompanionId] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const loadRooms = async () => {
    setIsFetching(true);
    setError(null);

    try {
      const result = await listRooms(userId);
      setRooms(result);
    } catch (err) {
      console.error("Failed to load rooms", err);
      setError(getErrorDetail(err));
    } finally {
      setIsFetching(false);
    }
  };

  useEffect(() => {
    loadRooms();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const handleCreateRoom = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!newDisplayName.trim()) return;

    setIsCreating(true);
    setCreateError(null);

    try {
      const room = await createRoom({
        user_id: userId,
        room_type: newRoomType,
        display_name: newDisplayName.trim(),
        companion_id: newCompanionId.trim() || undefined,
      });

      setRooms((prev) => [...prev, room]);
      setShowCreateModal(false);
      setNewRoomType("one_on_one_chat");
      setNewDisplayName("");
      setNewCompanionId("");
    } catch (err) {
      console.error("Failed to create room", err);
      setCreateError(getErrorDetail(err));
    } finally {
      setIsCreating(false);
    }
  };

  const handleDeleteRoom = async (roomId: string) => {
    setIsDeleting(true);
    setDeleteError(null);

    try {
      await deleteRoom(roomId);
      setRooms((prev) => prev.filter((r) => r.room_id !== roomId));
      setConfirmDeleteId(null);
    } catch (err) {
      console.error("Failed to delete room", err);
      setDeleteError(getErrorDetail(err));
    } finally {
      setIsDeleting(false);
    }
  };

  const handleSelectRoom = (room: RoomData) => {
    if (room.room_id === activeRoomId) return;
    onRoomChange(room.room_id, room.room_type);
  };

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800 p-5 text-gray-100">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-100">Rooms</h2>
            <p className="mt-1 text-sm text-gray-400">
              Switch between active rooms or create a new one.
            </p>
          </div>
        </div>

        {error ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
            <button
              type="button"
              className="ml-2 underline hover:no-underline"
              onClick={loadRooms}
            >
              Retry
            </button>
          </div>
        ) : null}

        {deleteError ? (
          <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {deleteError}
            <button
              type="button"
              className="ml-2 underline hover:no-underline"
              onClick={() => setDeleteError(null)}
            >
              Dismiss
            </button>
          </div>
        ) : null}

        {isFetching ? (
          <div className="text-sm text-gray-400">Loading rooms...</div>
        ) : rooms.length === 0 ? (
          <div className="text-sm text-gray-500">No rooms yet.</div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {rooms.map((room) => {
              const isActive = room.room_id === activeRoomId;

              return (
                <div key={room.room_id} className="relative">
                  {confirmDeleteId === room.room_id ? (
                    <div className="flex items-center gap-2 rounded-lg border border-red-700 bg-red-950/30 px-3 py-2 text-sm">
                      <span className="text-red-200">Delete?</span>
                      <button
                        type="button"
                        disabled={isDeleting}
                        className="rounded bg-red-700 px-2 py-0.5 text-xs font-medium text-white transition hover:bg-red-600 focus:outline-none focus:ring-2 focus:ring-red-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                        onClick={() => handleDeleteRoom(room.room_id)}
                      >
                        {isDeleting ? "..." : "Yes"}
                      </button>
                      <button
                        type="button"
                        disabled={isDeleting}
                        className="rounded border border-gray-600 px-2 py-0.5 text-xs text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                        onClick={() => setConfirmDeleteId(null)}
                      >
                        No
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-indigo-500/60 ${
                        isActive
                          ? "border-indigo-500 bg-indigo-900/40 text-indigo-200"
                          : "border-gray-600 bg-gray-900 text-gray-300 hover:border-gray-500 hover:bg-gray-800"
                      }`}
                      onClick={() => handleSelectRoom(room)}
                    >
                      <span className="text-base">
                        {ROOM_TYPE_ICONS[room.room_type]}
                      </span>
                      <span className="max-w-[120px] truncate">
                        {room.display_name}
                      </span>
                    </button>
                  )}

                  {confirmDeleteId !== room.room_id ? (
                    <button
                      type="button"
                      className="absolute -right-2 -top-2 flex h-5 w-5 items-center justify-center rounded-full border border-gray-600 bg-gray-800 text-xs text-gray-400 transition hover:border-red-500 hover:text-red-300 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
                      title="Delete room"
                      onClick={(e) => {
                        e.stopPropagation();
                        setConfirmDeleteId(room.room_id);
                      }}
                    >
                      &times;
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}

        <button
          type="button"
          className="flex items-center gap-2 self-start rounded-lg border border-gray-600 px-3 py-2 text-sm font-medium text-gray-300 transition hover:border-indigo-500 hover:bg-gray-700 hover:text-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
          onClick={() => {
            setCreateError(null);
            setShowCreateModal(true);
          }}
        >
          <svg
            className="h-4 w-4"
            viewBox="0 0 20 20"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
          >
            <path
              d="M10 4V16M4 10H16"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
          New Room
        </button>
      </div>

      {showCreateModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-md rounded-lg border border-gray-700 bg-gray-800 p-6 text-gray-100 shadow-xl">
            <h3 className="text-lg font-semibold">Create Room</h3>

            {createError ? (
              <div className="mt-3 rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-200">
                {createError}
              </div>
            ) : null}

            <form className="mt-4 flex flex-col gap-4" onSubmit={handleCreateRoom}>
              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Room type
                <select
                  value={newRoomType}
                  disabled={isCreating}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                    setNewRoomType(e.target.value as RoomType)
                  }
                >
                  {ROOM_TYPE_OPTIONS.map((rt) => (
                    <option key={rt} value={rt}>
                      {ROOM_TYPE_LABELS[rt]}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Display name
                <input
                  type="text"
                  value={newDisplayName}
                  disabled={isCreating}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="Room name"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setNewDisplayName(e.target.value)
                  }
                />
              </label>

              <label className="flex flex-col gap-2 text-sm font-medium text-gray-200">
                Companion ID (optional)
                <input
                  type="text"
                  value={newCompanionId}
                  disabled={isCreating}
                  className="rounded-lg border border-gray-600 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-60"
                  placeholder="Leave blank for default"
                  onChange={(e: ChangeEvent<HTMLInputElement>) =>
                    setNewCompanionId(e.target.value)
                  }
                />
              </label>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  disabled={isCreating}
                  className="rounded-lg border border-gray-600 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/60 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => {
                    setShowCreateModal(false);
                    setCreateError(null);
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isCreating || !newDisplayName.trim()}
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:bg-indigo-600 disabled:opacity-50"
                >
                  {isCreating ? "Creating..." : "Create"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export default RoomSwitcher;