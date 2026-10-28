import { useEffect, useRef, useSyncExternalStore } from "react";
import type { Companion, User } from "../api/client";
import { liveAssistantText } from "../avatar/liveText";
import ChatMessage from "./ChatMessage";

/** The assistant's in-progress spoken-turn reply. Re-renders on its own, not with the chat list. */
export function LiveAssistantBubble({
  companion,
  user,
}: {
  companion?: Companion | null;
  user?: User | null;
}) {
  const text = useSyncExternalStore(liveAssistantText.subscribe, liveAssistantText.get);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [text]);

  if (!text) return null;
  return (
    <>
      <ChatMessage message={{ role: "assistant", content: text }} companion={companion} user={user} />
      <div ref={endRef} />
    </>
  );
}

export default LiveAssistantBubble;
