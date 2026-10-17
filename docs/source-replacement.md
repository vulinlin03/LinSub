# Source replacement isolation

## URL import sessions

Each project key owns its own `VideoUrlImportCoordinator`. Entering another
project does not inherit a busy state, progress, inspected metadata or a cancel
event, even when both use the same Douyin URL. Authentication transport remains
the shared app-owned anonymous Douyin session; project destinations do not.

Download callbacks carry the owning coordinator. Its captured project/video
target follows the file into the background storage-import context. Completion
updates that coordinator, not whichever project is currently displayed, and
finishing an inactive project cannot close the current project's dialog.
Reopening a busy project's dialog restores its own URL rather than showing an
empty disabled field. Project deletion and application shutdown cancel only the
intended project/all owned tasks respectively.

## Source-bound assets and cache

Version 0.1.9 removed the old `input` directory while retaining file-map keys
such as `background_music`. This left the automatic audio mixer pointing to a
deleted file. The current replacement contract publishes a fresh file map;
music, watermark media, clone references and source-derived outputs cannot
implicitly carry over. Model choices are retained. New music can be selected
normally after replacement.

Input, legacy editor document, temp workspace and video metadata are one
replacement transaction. Old temp files are moved away from canonical lookup
paths before new metadata is published. If that move is locked, input/metadata
are rolled back instead of accepting a mixed old/new workspace. If deleting
the quarantined old files is deferred, they cannot be read as the new source's
OCR, subtitle, voice or preview files. Backups are retained if rollback itself
cannot finish; successful transactions log deferred cleanup.

The new source has a new generation, empty checkpoints/active artifacts and an
already-initialized migration marker, so old files cannot be adopted by legacy
artifact migration. Source reset clears its editor drafts/history, not other
projects' cache. A music-download callback captured before replacement is
rejected if its source generation/creation timestamp changed, or replacement
is currently pending. Its temporary download is cleaned up without attaching it
to the new source or replaying old asset history.

## Tests

Fixtures cover same-link parallel projects, late completion after navigation,
owner-specific cancellation/deletion, real QML dialog reopening, stale music
callbacks, deferred old-temp cleanup, Windows-lock rollback, and actual FFmpeg
decode/audio mixing after replacement in both Automatic and Manual projects.
No live user project is rewritten by these tests.
