import type { TraceDetail } from "../api/client";

// Where the companion's identity in the assembled prompt came from, as recorded
// by the backend's compile_context trace step. The assembled prompt itself
// remains the exact provider input; this only labels its layers.
export const IDENTITY_LAYERS: { key: string; label: string }[] = [
  { key: "configured", label: "Configured personality" },
  { key: "evolved", label: "Persistent evolved identity" },
  { key: "relationship", label: "Persistent relationship" },
  { key: "session", label: "This conversation (temporary)" },
];

export function identitySources(trace: TraceDetail): Record<string, unknown> | null {
  const step = trace.steps.find((item) => item.step_name === "compile_context");
  const metadata = step?.metadata as Record<string, unknown> | undefined;
  const sources = metadata?.identity_sources;
  return sources && typeof sources === "object" ? (sources as Record<string, unknown>) : null;
}
