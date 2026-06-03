export interface Session {
  session_id: string;
  title: string;
  created_at: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp?: string;
}

export interface ChatRequest {
  message: string;
  session_id: string;
}

export interface ChatResponse {
  session_id: string;
  response: string;
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
}

export interface ApiError {
  detail: string;
  status_code: number;
}
