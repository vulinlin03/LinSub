# Manual export state and progress

- Capture the idle Manual state before export queueing; persist it in the export-owned run journal. Cancellation restores it after current-run outputs are removed and previous artifact references restored.
- Waiting exports cancelled before any worker starts also restore their captured state. Source identity guards prevent restoring state into a replaced source. Auto cancellation and pause/resume remain unchanged.
- Old cancelled Manual export cards present as ready, without rewriting project data. Cancelled recognition/translation and Auto jobs still display cancelled.
- The export dialog and activity strip use one monotonic measured job meter: rendering 0–90%, verification/saving 90–99%, successful publication 100%. Copy/range-only jobs also cannot show 100% before the success event.
- Larger, bounded live-background capture removes coarse magnification; proportional blur radius retains the previous softness. Native blur initialization guards remain, one decoder, no disk-cache invalidation.

Verification: 150 passed, 1 skipped, 6 subtests; QML lint, Python import/name checks, whitespace checks; native GPU cold 4K switch and 60 rapid toggles; thumbnail/paused-frame pixels; isolated app startup. Source only, no build/push/publish.
