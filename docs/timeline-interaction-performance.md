# Timeline interaction performance

Pan/zoom changes presentation only. They do not change project clips, subtitle
timings, audio/OCR caches, source media, or export inputs.

- Subtitle, voice/layer and ruler Repeaters use stable, keyed ListModels.
  Viewport updates insert/remove/move rows instead of replacing every delegate.
  Model roles store only keys/indices; cue text, waveform and timings bind to
  current project data, including when data is replaced or reordered.
- The viewport includes 160 pixels of overscan, so clips entering the view are
  ready before reaching its edge. Offscreen subtitle delegates remain hidden.
- View updates are throttled at 16 ms rather than debounce-restarted at every
  pointer event. Continuous input cannot postpone all updates until it stops.
  Wheel deltas accumulate at that cadence and retain the pointer's time anchor.
- Ruler spacing uses a readable interval at the current pixel/time scale,
  including fit zoom below one pixel per second for long videos.
- Result/OCR context menus load only on applicable tracks. Thousands of voice
  clips no longer allocate unused result/OCR menus.
- Indexed cues and clips grouped by track are cached until document data
  changes; panning does not repeatedly clone whole subtitle records or scan
  every other track's clips.

## Verification

`tests/test_subtitle_timeline_viewport_ui.py` runs actual Qt QML windows in
isolated processes at 900/1800-pixel widths for a 42-minute source, 1000 pixels
for a one-hour source, and 1280 pixels for a short source. It verifies final
cues/voices at fit and zoom, retained delegate identities, continuous pan,
wheel accumulation and pointer anchoring, cue updates, reordered clip data,
and click selection without accidental timing edits. Existing editor tests
cover wheel zoom followed by timing dragging and result/source track behavior.

Run `python scripts/benchmark-timeline.py` for an isolated synthetic offscreen
QML benchmark: 1200 subtitles, 1200 voice clips, 14 zoom changes and 60 two-pixel
pan updates. The measured time includes model/delegate/event processing; it is
not an FPS measurement or a full running-project GPU benchmark.

Single observations on the development machine (2026-10-11):

| Operation | Before | After |
| --- | ---: | ---: |
| 14 zoom changes, total | 24817.0 ms | 1321.4 ms |
| 60 pan updates, total | 9532.2 ms | 404.7 ms |
| Existing delegate retained | No | Yes |

Hardware, rendering backend and other active work affect timings. Large first
loads/zoom-to-fit still create the newly required visible delegates. This is
not a guarantee of constant frame time on every machine.
## Preview playback lifecycle and overlay updates

Preview first-frame priming is not a user Play request. Result metadata updates
must not clear the priming latch while its decoder is running, or convert that
temporary decoder state into a deferred scrub/cache-swap resume. Explicit
pause, workspace hiding, project switching, and media release now cancel that
deferred intent. An intentional Play request still survives a usable cache swap.

The preview composition indexes overlay clips/assets when the editor document
changes. Playback ticks no longer scan unrelated subtitle and voice clips.
QML receives a separate overlay notification only when visual data changes;
static overlays are not reconstructed merely because the playhead advanced.
Time-dependent transforms and clip boundaries still update normally. Persistent
subtitle, voice, OCR, and export caches are unchanged.

An isolated synthetic check with 1,800 caption clips and one static overlay
measured 5,000 composition ticks at 387.3 ms before and 63.6 ms after this change.
Overlay-binding notifications fell from 5,000 to zero during those ticks. This
measures main-thread composition overhead, not playback FPS or responsiveness
under a real model workload. CPU/GPU saturation can still affect playback.

