import type {
  ApiError,
  ChatResponse,
  CharacterProfile,
  CreateSessionResponse,
  IdentityState,
  RelationshipConfig,
  StreamEvent,
} from "../types";

const BASE_URL = "";

async function parseErrorResponse(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ApiError>;

    return {
      detail: typeof body.detail === "string" ? body.detail : response.statusText,
      status_code:
        typeof body.status_code === "number" ? body.status_code : response.status,
    };
  } catch {
    return {
      detail: response.statusText,
      status_code: response.status,
    };
  }
}

async function assertOk(response: Response): Promise<void> {
  if (!response.ok) {
    throw await parseErrorResponse(response);
  }
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });

  await assertOk(response);
  return (await response.json()) as T;
}

function dispatchSseEvent(rawEvent: string, onEvent: (event: StreamEvent) => void) {
  const event: StreamEvent = {
    event: "message",
    data: "",
  };
  const dataLines: string[] = [];
  let hasEventField = false;

  for (const line of rawEvent.split(/\r?\n/)) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }

    const separatorIndex = line.indexOf(":");
    const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex);
    let value = separatorIndex === -1 ? "" : line.slice(separatorIndex + 1);

    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "event") {
      event.event = value;
      hasEventField = true;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  if (!hasEventField && dataLines.length === 0) {
    return;
  }

  event.data = dataLines.join("\n");
  onEvent(event);
}

export function createSession(): Promise<CreateSessionResponse> {
  return requestJson<CreateSessionResponse>("/api/sessions/", {
    method: "POST",
  });
}

export function sendMessage(
  message: string,
  sessionId: string,
): Promise<ChatResponse> {
  return requestJson<ChatResponse>("/api/chat/", {
    method: "POST",
    body: JSON.stringify({ message, session_id: sessionId }),
  });
}

export function getRelationshipConfig(
  sessionId: string,
): Promise<RelationshipConfig> {
  return requestJson<RelationshipConfig>(
    `/api/config/relationship/${sessionId}`,
    {
      method: "GET",
    },
  );
}

export function updateRelationshipConfig(
  sessionId: string,
  config: Partial<RelationshipConfig>,
): Promise<RelationshipConfig> {
  return requestJson<RelationshipConfig>(
    `/api/config/relationship/${sessionId}`,
    {
      method: "PUT",
      body: JSON.stringify(config),
    },
  );
}

export function getCharacterProfile(
  sessionId: string,
): Promise<CharacterProfile> {
  return requestJson<CharacterProfile>(`/api/config/character/${sessionId}`, {
    method: "GET",
  });
}

export function updateCharacterProfile(
  sessionId: string,
  profile: Partial<CharacterProfile>,
): Promise<CharacterProfile> {
  return requestJson<CharacterProfile>(`/api/config/character/${sessionId}`, {
    method: "PUT",
    body: JSON.stringify(profile),
  });
}

export function getIdentityState(sessionId: string): Promise<IdentityState> {
  return requestJson<IdentityState>(`/api/config/identity/${sessionId}`, {
    method: "GET",
  });
}

export function evolveIdentity(
  sessionId: string,
  context?: Record<string, unknown>,
): Promise<IdentityState> {
  return requestJson<IdentityState>(
    `/api/config/identity/${sessionId}/evolve`,
    {
      method: "POST",
      body: JSON.stringify(context === undefined ? {} : { turn_context: context }),
    },
  );
}

export function resetIdentityMood(sessionId: string): Promise<IdentityState> {
  return requestJson<IdentityState>(
    `/api/config/identity/${sessionId}/reset-mood`,
    {
      method: "POST",
    },
  );
}

export async function sendMessageStream(
  message: string,
  sessionId: string,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(`${BASE_URL}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  await assertOk(response);

  if (!response.body) {
    throw {
      detail: "Streaming response body is unavailable",
      status_code: response.status,
    } satisfies ApiError;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();

    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      dispatchSseEvent(part, onEvent);
    }
  }

  buffer += decoder.decode();

  if (buffer.trim() !== "") {
    dispatchSseEvent(buffer, onEvent);
  }
}
