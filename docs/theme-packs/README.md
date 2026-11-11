# Theme-pack workflow (v0.2)

ThemeLab keeps its Windows-local React/Vite + FastAPI stack. The legacy image
library, ADB serial targeting, screenshots and wallpaper staging routes remain
available. No cloud generation or companion application is introduced.

## Run and check

Install FFmpeg, including `ffmpeg` and `ffprobe`, on PATH. Alternatively set
`FFMPEG_PATH` and `FFPROBE_PATH` to their full executable paths. These are required
to validate MP4 content, codec, absence of audio, dimensions, timing and motion.
Install backend dependencies with `pip install -r backend/requirements.txt` and
run `npm install` in `frontend` after updating. Start with `start_windows.bat` or
the existing two-terminal instructions in the root README.

Development checks from repository root:

```sh
python -m pip install -r backend/requirements-dev.txt
python -m pytest
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```

Keep the backend bound to localhost. The app has no remote authentication and is
not intended to be published as a network service.

## Pack structure

```text
manifest.json
phone/live-wallpaper.mp4
watch/live-background.webp
```

Paths are relative to the manifest's directory. A ZIP may contain 1–30 disjoint
theme directories. Accepted manifest names, in precedence order per directory,
are `theme.standard.json`, `manifest.json`, and `theme.json`. Nested overlapping
theme directories are rejected. A standards-only ZIP without the media is not a
theme pack.

The authoritative manifest and saved-selection schemas are in
`backend/app/schemas`. `component-catalog.json` defines the optional input
inventory. Example manifests and selection are in this folder's `examples`.
The three example manifests refer to the previously supplied classic collection
media; they intentionally do not bundle those media or any provider apps.

Required: schema version, theme ID/title/version/palette, generation policy,
silent looping H.264 phone MP4 and looping animated WebP/GIF watch background.
Both animations are required even if the user applies to only one device.
Static assets never substitute for required animation. AOD animation is disabled.
The app has no existing generation service; this release validates imported
generation output rather than adding a speculative generator.

Example minimal manifest (replace metadata/checksums with the actual media):

```json
{
  "schema_version": "1.0.0",
  "theme": {
    "id": "my-theme", "title": "My Theme", "version": "1.0.0",
    "palette": {"background":"#080F19","foreground":"#F1E7D6","accent":"#C2A16B"}
  },
  "generation_policy": {
    "phone_live_required": true, "watch_live_required": true,
    "fetch_missing_optional_components": false
  },
  "phone": {"live_wallpaper": {
    "path":"phone/live-wallpaper.mp4", "mime_type":"video/mp4",
    "sha256":"REPLACE_WITH_64_LOWERCASE_HEX_DIGITS", "width":1080,"height":2340,
    "duration_seconds":8,"fps":30,"loop":true,"audio":false
  }},
  "watch": {"live_background": {
    "path":"watch/live-background.webp", "mime_type":"image/webp",
    "sha256":"REPLACE_WITH_64_LOWERCASE_HEX_DIGITS", "width":480,"height":480,
    "duration_seconds":6,"fps":10,"loop":true,"audio":false
  }}
}
```

Optional sections may be absent. Optional provider suggestions are distinct from
installed/compatible capabilities. Provider references never trigger purchases,
installations, permission grants or fetching the entire catalog. Style/placement
tokens are requests, not claims that stock providers can be restyled.

Extension keys must be namespaced, e.g. `org.example.widget_style`. Unknown major
schema versions are rejected; compatible 1.x versions must still validate against
the supported fields. Bump the theme version to change immutable pack content.

## Import validation and migration

Limits: 256 MB uploaded, 768 MB expanded, 128 MB per entry, 1024 entries,
compression ratio at most 250:1. Traversal, absolute/Windows-ambiguous paths,
symlinks/special files, encrypted ZIPs, duplicate/case-conflicting paths, duplicate
JSON keys, invalid checksums, undecodable content and incorrect media metadata
are rejected. MP4 limits are 30 seconds, 60 fps and 12 MP. Images/animations have a
96 MB decoded-frame budget. Watch animations must loop indefinitely and move.
Phone previews and the runtime wallpaper flow control MP4 looping; MP4 itself
does not contain an infinite-loop playback flag. Visual loop smoothness still
needs artistic review.

Validate every theme before committing a collection. Failed imports leave no
partial pack records or asset directories. Reimporting identical content returns
the existing record; conflicting content at the same ID/version is rejected.

Known migrations:

- MVP `id/name/wallpaper` manifests become incomplete drafts, preserving stills.
- `classic-theme-assets-v1` collections are normalized and media revalidated.
  Known classic IDs receive editable widget/complication recommendations.
- **Import existing legacy themes** copies local MVP themes; originals remain
  available through their existing API/UI. It never invents missing animations.

Imported packs and metadata live under `data/packs`, ignored by git. SQLite stores
pack identity, immutable manifest snapshots, separate selections, plans and jobs.
Media paths are server-owned UUID folders. Raw assets can only be served when
referenced by the manifest. Exports include the normalized manifest and referenced
assets, excluding private user selections and apply history.

## Selections and review

Saved user override > theme default. Phone/watch target switches are independent.
Each optional widget, complication slot, Tile and auxiliary setting has a toggle.
Required live media use device target switches rather than individual overrides.
Reset deletes no device resources; it only restores saved selection defaults.

Provider references may be chosen from discovered component names, or left for
the device picker. Bindings are persisted under the target ADB serial. The app
does not guess package names, infer provider data types from names, or assume
world-clock cities. An unavailable selected provider marks its item unsupported,
not the entire theme invalid. Complication categories are validated against the
slot options. Slot IDs/component IDs must be unique; bounds must stay within 0..1
and include an EMPTY state.

Review creates an immutable plan snapshot containing only selected components for
enabled targets. Changed selections require a new review. Plans are consumed once;
device conflicts are blocked while another job is running or awaiting interaction.

## Device adapters and truthful results

`device_adapters.py` provides an adapter registry with Android phone and Wear OS
implementations. It builds on the existing explicitly serial-scoped ADB wrapper.
Hardware role comes from device features; OS/model/resolution and launcher are
queried, not inferred from the theme. Initial intended hardware is the S22 Ultra
and Galaxy Watch8 Classic; adapters do not hard-code their dimensions.

Installed widget receiver, complication service and Tile service components are
queried with the corresponding documented intent actions. Queries may be unavailable
on some Android builds. Missing query support is **unknown**, not a claim that no
provider exists. Discovery does not establish data-type compatibility; the device
chooser/editor still checks that. Pair and authorize the watch independently;
phone ADB authorization is not watch authorization.

Automatic actions in this release:

- Import, validate, persist, export and preview assets.
- Resolve selections and query connected device capabilities/provider components.
- Stage selected assets under `/sdcard/Download/ThemeLab/<pack-id>/` on the correct
  device, comparing remote checksums where available and recording ownership.
- Open the phone wallpaper picker or watch Settings for live-background handoff.

Device/editor interaction:

- Phone: choose the staged MP4 from Gallery / wallpaper settings and confirm a
  supported destination. A generic Android device may need an already installed
  live wallpaper engine; no universal engine is supplied here.
- Widgets: add/configure via the launcher's widget picker. Exact placement/style
  depends on the provider/launcher; ADB is not a universal widget placement API.
- Watch: import animation into Watch Face Studio, add real time and matching
  complication slots, configure nonanimated AOD, test/build/deploy with Run on
  Device, then select the face. Copying WebP to watch storage does not install a
  watch face. Exported manifests are layout references, not compiled WFF projects.
- Complications: customize compatible slots on the installed face and choose
  available sources. Tiles use their separate Add Tiles/Galaxy Wearable flow.
- Other icon/font/sound/clock/layout/AOD settings follow the matching supported
  device/editor flow; no unsupported settings commands or APK installs are issued.

Every item is applied, pending_user_action, failed, skipped or unsupported.
Transferred files and requested settings activities remain pending. The explicit
**I confirmed this on the device** action records `verification=user_reported`;
it is not automatic device verification. Mixed outcomes are partial, not success.

Jobs run outside HTTP requests. Cancellation waits for an in-flight ADB action,
skips remaining/pending actions and retains staged resources. Retry executes only
failed items, preserving successes and pending actions. Backend restart turns
interrupted actions into failures requiring review/retry, retaining completed and
pending records. No completion is inferred from a timeout or absent callback.

Best-effort undo: after cancellation, **Remove unused staged copies** removes only
recorded ThemeLab-created copies whose content still matches, which no other job
references, and only when the job has no reported applied/pending item. Unknown,
changed or shared files are retained with an explanation. Existing widgets,
providers and user settings are never removed. This is staging cleanup, not
wallpaper/settings restoration; those APIs are not available in this release.

## API

- `GET /api/packs`, `POST /api/packs/import`, `POST /api/packs/migrate-legacy`
- `GET /api/packs/schema`, `GET /api/packs/devices/{serial}/capabilities`
- `GET /api/packs/{id}`, `GET /api/packs/{id}/assets/{path}`, `GET /api/packs/{id}/export`
- `GET|POST /api/packs/{id}/selection`, `POST /api/packs/{id}/selection/reset`
- `POST /api/packs/{id}/plan`, `POST /api/packs/{id}/jobs` with a plan ID
- `GET /api/packs/jobs`, `GET /api/packs/jobs/{id}`
- `POST /api/packs/jobs/{id}/cancel`, `/retry`, `/cleanup-staging`
- `POST /api/packs/jobs/{id}/items/{component}/confirm` with outcome and explicit
  `confirmed_on_device=true` when reporting completion

Original `/api/themes` and `/api/devices` routes remain intact.

## Verification boundaries

Tests cover real generated MP4/WebP decoding, optional omissions, malicious ZIPs,
metadata/hash failures, legacy drafts, transactional collections, selections,
independent targets, provider absence, disconnects, pending/user-reported states,
partial results, cancellation/restart/retry, protected cleanup, API integration
and React preview/selection/review interactions. Device adapters are mocked at the
hardware boundary in automated tests. Neither a physical S22 Ultra nor a Watch8
Classic is connected to this development environment. Chromium launch is blocked
by the environment's process/socket sandbox, so browser visual playback/layout
QA remains a local hardware/browser check; jsdom interaction tests do not claim
to replace it.

## Official platform references (checked 11 November 2026)

- [Android widget pinning](https://developer.android.com/develop/ui/views/appwidgets/discoverability)
- [WallpaperManager / live wallpaper confirmation](https://developer.android.com/reference/android/app/WallpaperManager)
- [Complication slots and source types](https://developer.android.com/training/wearables/wff/complications)
- [Complication source service](https://developer.android.com/reference/androidx/wear/watchface/complications/datasource/ComplicationDataSourceService)
- [TileService](https://developer.android.com/reference/androidx/wear/tiles/TileService)
- [Watch Face Studio animated assets](https://developer.samsung.com/watch-face-studio/user-guide/create.html)
- [Samsung video wallpaper flow](https://www.samsung.com/us/support/answer/ANS10001632/)
