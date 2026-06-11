export interface Session {
  session_id: string;
  title: string;
  created_at: string;
  message_count?: number;
}

export interface Message {
  message_id?: string;
  role: "user" | "assistant";
  content: string;
  timestamp?: string;
  turn_id?: string;
}

export interface ChatRequest {
  message: string;
  session_id: string;
}

export interface ChatResponse {
  session_id: string;
  response: string;
  turn_id?: string;
  step_results?: Array<{
    step_name: string;
    status: string;
    detail?: string;
  }>;
}

export interface StreamEvent {
  event: string;
  data: string;
}

export interface CreateSessionResponse {
  session_id: string;
  title?: string | null;
  created_at?: string | null;
}

export interface RelationshipConfig {
  type: "friend" | "mentor" | "peer" | "partner" | "custom";
  tone: "warm" | "neutral" | "formal" | "playful" | "sarcastic";
  directness: number;
  warmth: number;
  initiative_level: number;
  content_level: "light" | "moderate" | "deep" | "intellectual";
}

export interface CharacterProfile {
  name: string;
  backstory: string;
  personality_sliders: Record<string, number>;
  humor_style: "dry" | "witty" | "silly" | "dark" | "none";
  communication_style:
    | "verbose"
    | "concise"
    | "storyteller"
    | "question_driven"
    | "aphoristic";
  favorite_topics: string[];
}

export interface IdentityState {
  fixed: Record<string, unknown>;
  slow: Record<string, unknown>;
  mood: Record<string, unknown>;
}

export interface ApiError {
  detail: string;
  status_code: number;
}
