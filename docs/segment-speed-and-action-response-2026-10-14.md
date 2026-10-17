# Segment clock and action response regression — 2026-10-14

Source-only changes; no application build, commit, push or publish.

## Cached segment clock

The reported defect was present in the cached-render branch. Result clips and
editor coordinates remain on the original sequence clock; an existing full
render has already applied output speed to both picture and audio.

The cached cutter now maps `start / speed` and `(end - start) / speed` using
floating-point seconds, validates against that rendered clock, and does not
apply tempo again. Original millisecond coordinates remain in export history.
The uncached branch still composes the original window before applying speed.

Real tests create a 60-second source with a red 40–50 second interval and reuse
a complete render at 0.5x, 1x, 2x and 3x. The selected interval is red in each
export and lasts respectively 20, 10, 5 and approximately 3.333 seconds. Tests
also verify original history coordinates, intact render bytes and released pins.

## Action response

- Manual enqueue checks only the requested tool's prerequisite, rather than
  computing every voice/audio/export signature and notice again.
- Manual subtitle and voice-presence checks on Qt use structural peeks;
  consuming workers retain cryptographic verification.
- Speculative model retirement moved from the Qt enqueue slot to the worker,
  before model use. Constrained-machine release barriers and startup pause
  checks remain in place.
- One read-only tool-state calculation can reuse an editor document. This is
  thread-local and scoped to the function call, not a persistent media cache.
  Path identity, size, modification and change times prevent reuse after edits;
  missing/deleted files are rejected before lookup.
- Processing, voice-confirmation and export controls share a deferred action
  component. They immediately show a preparing state and reject duplicate
  clicks, then dispatch after a short UI interval. Captured context and owner
  checks prevent a deferred click from targeting a different selected project.
  Hidden/dismissed controls drop their pending action. This is UI feedback, not
  a fabricated processing-queue or cache state.

## Source edit intermediate

Manual's video-only source compiler can now use the existing verified hardware
encoder selector, with a single CPU fallback and cancellation checks. Timeline
trim/concat/gap/tail filters, pixel format, duration and CRF/CQ setting stay the
same. This accelerates the intermediate pass; it does **not** promise to remove
all intermediates. Cuts/reordering plus separate overlays can still require
three encodes. Selected-window assembly remains bounded to that window.

## Verification

Coverage includes real FFmpeg cuts, edited source ordering, overlays, speed,
hardware failure fallback, cache integrity, cancellation and restart ownership,
CPU/GPU/resource matrices, warm-up pause/resume, Qt component dispatch/duplicate
clicks and edit-aware document reads. The primary combined regression run passed
403 tests with one platform-specific skip and six subtests; additional Manual
voice/dialog checks passed. `qmllint` and Python undefined-name checks passed.
