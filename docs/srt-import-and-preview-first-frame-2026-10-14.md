# SRT input/output and first-frame preview

## Manual SRT

- Workspace: Import SRT → Remove SRT → Export SRT. All refuse work while processing, importing media, or exporting; import/remove also guard pending subtitle saves.
- Import validates UTF-8/BOM and UTF-16, cue ordering, positive durations, nonempty content, and known source duration. Limits: 8 MiB / 50,000 cues. Basic SRT formatting tags become plain text.
- The source captions are copied into a project-owned input revision and bound to the source video's file identity. External file changes/deletion cannot change the project. Broken managed input reports an error, never falls back to Whisper silently.
- Recognize & translate publishes recognition from imported cues; no Whisper, separation prerequisite, Whisper resource-pack requirement or recognition warm-up. Translation receives original timestamps and stable imported cue IDs.
- Import publishes editable captions immediately on preview/timeline, mapping source cues through current source cuts. It clears prior text/voice consumers, but preserves source, OCR, music, watermark and source edit decisions. Supported imported captions can generate speech directly without translation; language validation precedes TTS startup.
- Remove SRT clears captions/voice to an empty working state, not a restore of old results. Legacy descriptors without a baseline are supported. Branch generations advance to prevent old recognition/translation/voice cache resurrection; legacy migration is marked complete. Only the exact owned SRT revision is discarded, with retry for Windows locks. External SRT files and historical immutable cache files are not deleted.
- Source replacement resets the input map and therefore removes the SRT binding. Restart/cancel keeps imported input, like other user inputs.
- Export options: result (default), original language, both. Pair filenames: `<name>.source.srt` and `<name>.result.srt`. Original captions follow source edit decisions; translated result follows editable caption clips. Both retain the existing editor-clock SRT convention, not output-speed-adjusted timestamps.
- Stage both outputs before publication; restore previously existing destinations if the second publication fails. If Windows also prevents restoration, retain the recovery backup rather than discard user data.

## Button and portrait preview

- The independent separation button now uses DeferredActionButton with a captured video-ID guard, like the other processing buttons.
- First portrait frame: create only the small capture shell synchronously. Keep native blur creation asynchronous and gated on its prerequisite. While shaders load, display a bounded source-frame background instead of black; refresh capture after blur creation, including paused playback.
- One existing decoder/sink. Follow-up quality fix increases the bounded capture to at most 960 × 540; the blur radius scales with capture resolution to preserve its previous apparent softness. No media/OCR/audio/cache format changes.

## Verification

- Combined relevant regression run: 273 passed, 5 subtests passed.
- Native Windows/GPU pixel tests: thumbnail, paused frame, cold 4K switch, 60 rapid toggles, unavailable/pending blur fallback and teardown.
- Actual SRT dialog tested with an isolated controller; preview image inspected at `build/ui-qa-srt-portrait/srt-export.png`.
- QML lint, Python undefined-name/import checks and whitespace checks pass.
- No build, push, publish, or changes to user projects.

### Immediate import / empty-state removal follow-up

- 108 relevant regression tests and 3 subtests passed, including actual Qt import/export dialogs, legacy descriptor removal, reruns, language guards, rollback and locked owned-input cleanup.
- Verified removal against an isolated copy of the actual `fkfk` metadata/editor document: 48 captions → 0; voice layers cleared; current source edit decisions retained. Original project metadata and editor file remained byte-identical.
- Historical immutable caches are detached, not destructively cleared. No source/OCR/music/watermark input is a cleanup target.
