import { useEffect, useState } from "react";
import { fetchTraceByTurn, TraceDetail } from "../api/client";
import { IDENTITY_LAYERS, identitySources } from "./identitySources";

interface PromptInspectorProps {
  turnId: string | null;
}

function formatLatency(ms: number) {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatUsd(micros: number) {
  return `$${(micros / 1000000).toFixed(4)}`;
}

export default function PromptInspector({ turnId }: PromptInspectorProps) {
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const [promptOpen, setPromptOpen] = useState(true);
  const [responseOpen, setResponseOpen] = useState(true);
  const [identityOpen, setIdentityOpen] = useState(true);

  useEffect(() => {
    if (!turnId) {
      setTrace(null);
      setError(null);
      return;
    }

    let isMounted = true;
    setLoading(true);
    setError(null);
    setTrace(null);

    fetchTraceByTurn(turnId)
      .then((data) => {
        if (isMounted) setTrace(data);
      })
      .catch((err) => {
        if (isMounted) setError(err.detail || err.message || String(err));
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [turnId]);

  if (!turnId) {
    return (
      <div className="flex h-full items-center justify-center p-6 bg-gray-900 border-l border-gray-700 text-gray-400">
        Click a message to inspect its prompt and response.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-gray-900 border-l border-gray-700 overflow-y-auto">
      <div className="sticky top-0 z-10 border-b border-gray-700 bg-gray-900/95 p-4 backdrop-blur">
        <h2 className="text-lg font-semibold text-gray-100">Prompt Inspector</h2>
        {turnId && <div className="text-xs text-gray-500 mt-1">Turn: {turnId}</div>}
      </div>

      <div className="flex-1 p-4">
        {loading && (
          <div className="flex items-center justify-center p-8">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent"></div>
          </div>
        )}

        {error && (
          <div className="rounded-lg bg-red-900/40 p-4 text-sm text-red-200 border border-red-800">
            {error}
          </div>
        )}

        {trace && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="rounded-lg bg-gray-800 p-3 border border-gray-700">
                <div className="text-gray-500 text-xs uppercase mb-1">Model</div>
                <div className="text-gray-200">{trace.modelId || "N/A"}</div>
              </div>
              <div className="rounded-lg bg-gray-800 p-3 border border-gray-700">
                <div className="text-gray-500 text-xs uppercase mb-1">Task</div>
                <div className="text-gray-200">{trace.taskClass || "N/A"}</div>
              </div>
              <div className="rounded-lg bg-gray-800 p-3 border border-gray-700">
                <div className="text-gray-500 text-xs uppercase mb-1">Latency</div>
                <div className="text-gray-200">{formatLatency(trace.totalLatencyMs)}</div>
              </div>
              <div className="rounded-lg bg-gray-800 p-3 border border-gray-700">
                <div className="text-gray-500 text-xs uppercase mb-1">Cost / Tokens</div>
                <div className="text-gray-200">
                  {formatUsd(trace.costUsdMicros)} ({trace.promptTokens}P + {trace.completionTokens}C)
                </div>
              </div>
            </div>

            <div className="rounded-lg border border-gray-700 overflow-hidden">
              <button 
                type="button"
                className="w-full flex items-center justify-between bg-gray-800 px-4 py-3 text-left font-medium text-gray-200 hover:bg-gray-700 focus:outline-none"
                onClick={() => setPromptOpen(!promptOpen)}
              >
                <span>Assembled Prompt</span>
                <span className="text-gray-500">{promptOpen ? "▼" : "▶"}</span>
              </button>
              {promptOpen && (
                <div className="bg-gray-950 p-4 max-h-[500px] overflow-y-auto">
                  <pre className="whitespace-pre-wrap font-mono text-xs text-gray-300">
                    {trace.prompt || "No prompt recorded."}
                  </pre>
                </div>
              )}
            </div>

            {identitySources(trace) && (
              <div className="rounded-lg border border-gray-700 overflow-hidden">
                <button
                  type="button"
                  className="w-full flex items-center justify-between bg-gray-800 px-4 py-3 text-left font-medium text-gray-200 hover:bg-gray-700 focus:outline-none"
                  onClick={() => setIdentityOpen(!identityOpen)}
                >
                  <span>Identity Sources</span>
                  <span className="text-gray-500">{identityOpen ? "▼" : "▶"}</span>
                </button>
                {identityOpen && (
                  <div className="bg-gray-950 p-4 space-y-3">
                    <p className="text-xs text-gray-500">
                      Layers composed into the assembled prompt above.
                    </p>
                    {IDENTITY_LAYERS.map(({ key, label }) => {
                      const value = identitySources(trace)?.[key];
                      return (
                        <div key={key}>
                          <div className="text-gray-500 text-xs uppercase mb-1">{label}</div>
                          <pre className="whitespace-pre-wrap font-mono text-xs text-gray-300">
                            {value == null ? "Not used for this turn." : JSON.stringify(value, null, 2)}
                          </pre>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            <div className="rounded-lg border border-gray-700 overflow-hidden">
              <button 
                type="button"
                className="w-full flex items-center justify-between bg-gray-800 px-4 py-3 text-left font-medium text-gray-200 hover:bg-gray-700 focus:outline-none"
                onClick={() => setResponseOpen(!responseOpen)}
              >
                <span>Raw LLM Response</span>
                <span className="text-gray-500">{responseOpen ? "▼" : "▶"}</span>
              </button>
              {responseOpen && (
                <div className="bg-gray-950 p-4 max-h-[500px] overflow-y-auto">
                  <pre className="whitespace-pre-wrap font-mono text-xs text-gray-300">
                    {trace.rawResponse || "No raw response recorded."}
                  </pre>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
