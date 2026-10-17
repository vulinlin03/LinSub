# Export performance regression check — 2026-10-14

Baseline: `68fd077`. Tests use generated media and a separate temporary runtime;
no user project or existing cache is changed.

## Changes

- Full exports verify SHA-256 while copying, rather than pre-reading the render
  in both the desktop worker and export service. The destination is still read
  back and verified before atomic publication. Source identity, cache revision,
  overwrite confirmation and pins remain checked.
- Verification is cancellable; byte-progress notifications are emitted only
  when their integer percentage changes.
- Manual and uncached segment exports opt into no-op passthrough for absent
  overlays and unchanged resolution/speed. Only fresh producer-owned staging
  files are renamed; source media and immutable cache files are never moved.
- Overlay, resolution/speed and cached segment encoding can use the existing
  tested NVENC/QSV/AMF selector. Failed hardware encoding retries once on CPU;
  cancellation never starts a retry, and progress cannot regress during retry.
- Small finishing/cut passes (at most 720p pixel area, at most 30 output seconds)
  stay on CPU to avoid GPU startup overhead. The main renderer and AI device
  selection are unchanged.
- Auto and normal Manual renders fuse final resolution and output speed into
  the existing encode, after caption/watermark/cleanup drawing. Manual image
  overlays fuse the same transform into their own composition pass instead of
  adding a third finishing encode. Audio tempo is applied to the original
  mixed WAV before AAC encoding, not after another lossy AAC generation.
- Production patch removal reuses one constant feather mask per layer. Only
  actual source pixels update each frame; RGB per-pixel expressions no longer
  run throughout the video. Blur strength and cleanup layer timing stay intact.
- First-time OCR sampling of HD-or-larger sources lasting at least 5 minutes
  uses 36 sequential midpoint seeks with two decoder threads, rather than
  decoding the complete video through an fps filter. Short/small videos retain
  the existing sampler. Existing OCR cache format/version/state is unchanged;
  a valid cache returns before decoding or inference.

### Encode counts

- Export a valid full render cache: zero video encodes (verified copy).
- Auto or ordinary Manual, including quality/speed changes: one video encode.
- Manual with separate image/video/text editor overlays: normally two video
  encodes, with final transforms fused into the second. A nontrivial edited
  source map (cuts/reordering/gaps) can still need its separate source-sequence
  compilation before these passes. Hardware failure can add one CPU retry.
- A selected result segment stays bounded to that segment; no AI rerun or
  whole-video render is introduced.

## Measurements on the development machine

Run the benchmark explicitly against the baseline:

```powershell
.venv/Scripts/python.exe scripts/benchmark-export.py --baseline-ref 68fd077
.venv/Scripts/python.exe scripts/benchmark-export.py --baseline-ref 68fd077 --duration 60 --width 1920 --height 1080 --size-mib 128
.venv/Scripts/python.exe scripts/benchmark-export.py --baseline-ref 68fd077 --production --duration 12 --width 3840 --height 2160 --size-mib 64
```

| Work measured | Before | After |
| --- | ---: | ---: |
| Verified 256 MiB export copy, median of 3 | 0.860 s | 0.654 s |
| Progress events for that copy | 257 | 70 |
| 720p / 20 s source, finishing at 2x | 0.933 s | 0.903 s |
| 1080p / 60 s source, finishing at 2x | 4.701 s | 3.269 s |
| 4K / 12 s source → 1080p-high at 2x, captions + patch cleanup | 10.170 s | 3.117 s |

The 1080p run used `h264_nvenc`; the short 720p run deliberately used `libx264`.
Decoded audio PCM before/after was identical in both speed tests. The requested
quality setting remains the same; hardware and software video encoders need
not produce identical video bitstreams. The copy benchmark clears the app's
validation cache before each run, **not** the operating system's disk cache.
These are individual-stage timings, not promises about total export time on
other hardware, media, resolutions or disks.
The production measurement includes render and final transformation, excluding
AI recognition/translation/voice generation and the external verified copy.
It is a 12-second generated sample, not an extrapolated long-video timing.

## Safety coverage

Regression tests cover full copy, real bounded render and cached cut, overlay
timing, output speeds, audio/video synchronization, 9:16 output, corrupt source
and destination data, changed source/destination during copying, cancellation
during verification, hardware failure fallback and cancellation without retry.
Default callers still materialize an independent output file; only private
staging producers opt into passthrough.
Additional real tests check 4K-to-HD in one encode, preservation of voice pitch,
caption clock scaling, overlay audio sync, first/middle/last patch pixels,
bounded OCR seeks, unchanged OCR cache hits and cancellation between decoders.
One pre-existing Manual voice test fixture was completed with its missing
subtitle model video_id; no voice-production logic changed for that correction.
