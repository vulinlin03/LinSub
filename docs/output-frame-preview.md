# Output-frame preview

Manual canvas framing remains a live QML operation. It uses the existing
video sink or initial thumbnail; it does not decode a second copy of the
video, render the entire timeline, or invalidate subtitle, voice or OCR caches.
Export composition and its FFmpeg background blur are unchanged.

## Cold-switch crash (2026-10-14)

The desktop crash at 00:40:57 had Windows exception `0xc0000005` in
`Qt6Core!QObject::setParent`, with `Qt6QuickEffects`/`QQuickMultiEffect`
initialization and QML Loader creation on the faulting stack. The app log
immediately before it reported `QQmlComponent: Component is not ready`.
Qt 6.11.1's MultiEffect creates `qrc:/data/BlurItem.qml` synchronously and
dereferences its result without checking readiness/null. This is consistent
with the dump; the full desktop crash has not been deterministically reproduced.

The original MultiEffect appearance is retained: `blur=1`, `blurMax=16`,
no automatic padding, and the same cover scale and low-resolution capture
geometry (`min(270, 480 * aspect)` by width/aspect). The temporary replacement
shader was removed because its appearance did not match the original blur.

A hidden asynchronous Loader first loads `qrc:/data/BlurItem.qml` in the same
QML engine. Only after this prerequisite reaches `Loader.Ready` can another
asynchronous Loader instantiate MultiEffect. The live prerequisite keeps its
compiled component referenced while the effect is used; Loading/Error states
never enter the unchecked native path. This uses an internal Qt resource, so
its availability is explicitly tested against the pinned Qt runtime.

Conditional Loaders release both resources outside visible blur mode. The
capture remains bounded at 270 by 480 pixels, independent of source resolution,
preview window size and video duration. Frame/geometry updates are coalesced
by a 33 ms single-shot timer; paused frames still refresh without playback.

## Verification

- Isolated cold QML process, 4K source frame, 60 rapid mode/visibility/size
  switches, including switches before asynchronous incubation completes.
- Actual Windows graphics rendering checks pixels in the background bands,
  thumbnail-to-video handoff, blur/crop/original geometry and paused refresh.
- Same video sink across switches; unused background Loader owns no item.
- Simulated missing prerequisite destroys/disables the effect without losing
  the video frame, and restoration enables the effect again.
- Existing proxy fingerprint/cache tests remain unchanged and run alongside
  long-preview and real bounded FFmpeg export tests.

The isolated 4K test measured about 5–10 ms to finish initial background QML
creation on this development machine. This is not a promise for total project
opening time or every GPU/driver.

Qt source reference:
[MultiEffect 6.11.1](https://github.com/qt/qtdeclarative/blob/v6.11.1/src/effects/qquickmultieffect.cpp#L1607-L1642).
