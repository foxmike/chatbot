import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AvatarPopup } from "./components/AvatarPopup";
import "./index.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element not found");
}

const searchParams = new URLSearchParams(window.location.search);
const isAvatarView =
  searchParams.get("view") === "avatar" ||
  window.location.pathname.endsWith("/avatar") ||
  window.location.pathname.endsWith("/avatar.html");

createRoot(rootElement).render(
  <StrictMode>
    {isAvatarView ? (
      <AvatarPopup
        initialCompanionId={searchParams.get("companion_id")}
        initialSessionId={searchParams.get("session_id")}
        initialRoomId={searchParams.get("room_id")}
      />
    ) : (
      <App />
    )}
  </StrictMode>
);
