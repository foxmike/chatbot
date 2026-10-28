# Avatar portrait-loading interface (chatbot ↔ AVTR-1 media runtime)

Owner: portrait service — this workstream (chatbot/avatar/).
Media runtime (AVTR/Qwen workers) — Sonnet. WebSocket coordinator — Codex.
React avatar popup — Gemini.

## Component

`chatbot/avatar/portraits.py` — `PortraitService` (plus `PortraitCache`,
`PreparedPortrait`, and the `PortraitError` hierarchy exported from
`chatbot/avatar/__init__.py`). The service has **no** dependencies on LLM
turns, Qwen inference, browser audio, the WebSocket coordinator, or the
TensorRT runtime. It only reads chatbot's own database.

## API

```python
from chatbot.avatar import PortraitService, PortraitError, ...

service = PortraitService(async_session_factory)   # one per process is fine

prepared = await service.get_portrait(
    user_id,                 # MUST be the authenticated user's id (CurrentUser.user_id)
    session_id="...",        # optional: companion attached to this chat session
    companion_id="...",      # optional: explicit companion (takes precedence)
    refresh=False,           # True bypasses the cache after a known portrait change
)
service.invalidate(user_id, companion_id=None)     # drop cached entries
```

`PreparedPortrait` fields: `image_id`, `companion_id`, `source_mime_type`,
`width`, `height`, `png_bytes` (AVTR-1-ready PNG; alpha preserved when the
source had it), `source_created_at`, `cache_key`.

### Failure contract

Every expected failure is a `PortraitError` subclass with a stable `code`:

| Exception                | `.code`                 | Meaning |
|--------------------------|-------------------------|---------|
| `PortraitNotFoundError`  | `companion_not_found`   | user/session resolves to no companion (or not owned → no existence leak) |
|                          | `session_not_found`     | session id unknown or owned by another user |
|                          | `no_portrait`           | companion exists but has no `avatar_url` |
|                          | `image_not_found`       | referenced `ImageMemory` row missing or has no stored bytes |
| `PortraitAccessError`    | `portrait_access_denied`| row ownership mismatch (defense in depth) / invalid user identity |
|                          |                         | image owned by user but attached to a *different* companion |
| `PortraitFormatError`    | `portrait_unusable`     | corrupt/truncated bytes, unsupported format (only PNG/JPEG/WEBP accepted), external URL reference, or image below 64 px (unsuitable for face detection/animation) |
|                          | `preparation_failed`    | unexpected exception during decode/re-encode, wrapped so it can never crash the app |

Suggested API/WS mapping: `portrait_not_found` → 404 / `portrait.unavailable`,
`portrait_access_denied` → 404 (do not distinguish for the client),
`portrait_unusable`/`preparation_failed` → 422 / `portrait.error` with
`detail` from `str(exc)`. Never return 200 with raw bytes for a failure.

### Resolution rules

1. `companion_id` given → that companion, filtered on the authenticated
   `user_id` (cross-user ids resolve to "not found").
2. else `session_id` → the `Session` row filtered on `user_id`; its
   `companion_id` is then loaded (and ownership re-checked — a session
   cannot select another user's companion).
3. else → the user's first companion (same convention as
   `chatbot/api/visual.py::_load_appearance_manager_for_user`).

The backing image is `Companion.avatar_url` parsed as
`/api/visual/images/{image_id}` → `ImageMemory` row filtered on the
authenticated user. Ownership is enforced by query *and* re-verified on the
loaded row, so another user's private portrait is unreachable through this
service.

### Cache

In-process LRU (`PortraitCache`, 32 entries, 300 s TTL) keyed on
`(user_id, companion_id, avatar_url)`. A cache hit skips the image-row
fetch, decode, and PNG re-encode entirely. Safety: generating a new
portrait changes `avatar_url` (it embeds the new `image_id`), so stale
entries are never served; only validated PNG bytes are cached. Not shared
across processes — each worker caches independently. After an out-of-band
portrait mutation, call `invalidate()` or `get_portrait(refresh=True)`.

## Portrait dimensions and proportion standard

New companion portraits request **1280×720 (16:9)**, matching the installed
AVTR pipeline's source/output canvas. The gallery explicitly requests those
dimensions and the visual generation API uses them as defaults. OpenRouter
receives `image_config.aspect_ratio = "16:9"`; a provider may return another
native resolution at that ratio. Explicit custom image dimensions remain supported.
Frame a single, centered, front-facing head and shoulders with the whole head
visible and enough margin for animation. Do not ask for a square face crop merely
because AVTR's internal decoder produces a 512×512 crop.

The actual installed AVTR code was inspected at
`/home/mike/dev/tools/avtr-1/src/avtr1_renderer/`:

- `pipeline.py::Pipeline.from_artifacts` defaults to `out_size=(720, 1280)`
  in **height, width** order and passes those dimensions to `AvatarLoader`.
- `avatar_loader.py::AvatarLoader.load` directly resizes a mismatched source
  to `out_w × out_h` **before face detection**. Passing an existing square
  portrait unchanged therefore widens the face; browser CSS cannot undo it.
- Face registration subsequently extracts a landmark-aligned 512×512 crop
  and a 256×256 appearance/motion input. Those are internal tensors, not
  the required whole-portrait aspect ratio. MODNet's 512×288 tensor is also
  an internal processing size, not the portrait generation standard.

Chatbot's worker now prepares a private PNG at the actual loader dimensions:
use **one uniform scale**, retain the **entire source**, and center it with
padding. Never resize width and height independently or crop the face to fill
an arbitrary frame. RGB padding matches the popup background (`#1e232d`);
RGBA padding stays transparent. Native-sized frames are copied without resampling.
Existing square, tall, wide, uploaded and generated portraits all work without
regeneration or rewriting database images. EXIF orientation is applied by the
portrait service before PNG encoding; its dimensions describe the oriented source.
The worker, not the source image, supplies AVTR's padded canvas.

The popup image fills its available stage and uses `object-contain`, keeping
proportions intact as the window changes size without overflowing the controls.

### Targeted verification

- `tests/avatar_media/test_avatar_worker.py`: square/tall/wide/native inputs,
  circular-feature proportions, full-source retention, alpha and custom loader size.
- `tests/test_avatar_portraits.py`: source normalization, EXIF orientation and
  existing portrait-service coverage.
- `tests/test_portrait_dimensions.py`: 1280×720 API defaults, 16:9 provider request,
  and explicit square image compatibility.
- Real OpenCV preprocessing of an existing 1920×1920 companion image confirms a
  centered 720×720 source in a 1280×720 frame. No AVTR/Qwen model is loaded for this check.
- Browser rendering checks use the actual popup with preprocessed image fixtures at
  920×800, 620×450 and 1200×1000. No speech requests or conversation writes are needed.

## AVTR-1 portrait-loading findings

Sources read: `my_avatar/python/my_avatar/avatar_worker.py`,
`avtr-1/src/avtr1_renderer/pipeline.py`, `avtr-1/src/avtr1_renderer/avatar_loader.py`.

1. **Portrait format expected by AVTR-1.** The worker is launched with the
   portrait path as `argv[1]`, reads it with `cv2.imread(..., IMREAD_UNCHANGED)`,
   writes it as `{portraits_dir}/custom.png`, and calls
   `Pipeline.from_artifacts(avatar_ids=["custom"], portraits_dir=...)`.
   Any PNG/JPEG input works; the prepared PNG from `PortraitService` can be
   written to disk and handed to the worker verbatim.

2. **Can a portrait be loaded into the running worker without restarting
   TensorRT?** Yes at the library level: `AvatarLoader` owns the five
   engines (insightface det, landmark106/203, appearance, plus the
   pipeline's avtr1 encode/decode and renderer engines) and is documented
   as *"construct once per process and reuse across portraits"* —
   `AvatarLoader.load(portrait_path, avatar_id=...)` runs per-portrait
   registration (face detect → bbox, motion extraction, crop calibration)
   against already-loaded engines. **No** at the current worker-IPC level:
   `avatar_worker.py` accepts only `speak` / `interrupt` / `listen` / `stop`
   and loads a single portrait at startup. Swapping a portrait without a
   process restart therefore requires a small extension to the worker's
   command set (e.g. `{"type": "portrait", "avatar_id": "...",
   "png_b64": "..."}` → `AvatarLoader.load` → `{"type": "portrait_loaded",
   "avatar_id": ...}`), to be implemented by the media-runtime owner.
   Restarting the worker does *not* rebuild TensorRT engines — engines are
   prebuilt artifacts loaded from disk (`get_trt_engine_path` +
   `load_engine`), so a restart costs engine *load* time only.

3. **Face-detection failure behaviour.** `AvatarLoader.load` raises
   `RuntimeError("No face detected in source image.")` for portraits
   without a detectable face. In the current standalone worker this aborts
   process startup. The portrait service mitigates this upstream by
   rejecting sub-64 px and corrupt images, but a valid-looking portrait can
   still fail detection — the integrated media runtime must catch worker
   errors for the `portrait` command and fall back to the previous avatar
   (or an idle state) instead of crashing. The portrait service itself
   never runs face detection and never raises non-`PortraitError`
   exceptions for expected failures.

4. **RGBA.** The loader auto-detects a 4-channel portrait as a matting
   candidate (MODNet path). `PortraitService` preserves alpha when the
   source image has it and otherwise emits RGB, so generated portraits
   (RGB JPEG/PNG) take the non-matting path, matching standalone behaviour.

## Boundary guarantees (requirement 12)

* The portrait service does not touch LLM turns, Qwen inference, browser
  audio, or the WebSocket coordinator.
* The media runtime consumes only `PreparedPortrait.png_bytes` (written to
  a temp/portrait dir as `{avatar_id}.png`, or delivered via the worker
  `portrait` command above).
* No TensorRT engine is rebuilt, downloaded, or modified anywhere in this
  code path.