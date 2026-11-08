import test from "node:test";
import assert from "node:assert/strict";

import type { TraceDetail } from "../src/api/client.ts";
import { IDENTITY_LAYERS, identitySources } from "../src/components/identitySources.ts";

function trace(steps: Record<string, unknown>[]): TraceDetail {
  return { steps } as unknown as TraceDetail;
}

test("Prompt Inspector: reads identity sources from the compile_context trace step", () => {
  const sources = {
    configured: { name: "Mercury" },
    evolved: { trait_adjustments: { openness: 0.1 } },
    relationship: { settings: { type: "mentor" }, stage_shown: false },
    session: { identity_tiers: ["slow", "mood"] },
  };
  const found = identitySources(
    trace([
      { step_name: "retrieve", metadata: { snippet_count: 0 } },
      { step_name: "compile_context", metadata: { identity_sources: sources } },
    ]),
  );

  assert.deepEqual(found, sources);
  assert.deepEqual(
    IDENTITY_LAYERS.map((layer) => layer.key),
    ["configured", "evolved", "relationship", "session"],
  );
});

test("Prompt Inspector: shows nothing when a turn recorded no identity sources", () => {
  assert.equal(identitySources(trace([])), null);
  assert.equal(identitySources(trace([{ step_name: "compile_context", metadata: {} }])), null);
});
