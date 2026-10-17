# Media and resource packaging — 0.1.10 technical review

Reviewer: Codex. Date: 2026-10-11 (Asia/Saigon). Scoped technical packaging
review, not independent legal or patent certification. The owner requested
publication and explicitly authorized committing and pushing the tested source
for 0.1.10 in this conversation, including the supplied Home/Manual screenshots.

Against the published 0.1.9 source, dependency locks, media recipes, model
checkpoints, preview voices, fonts, browser delivery and license terms are
unchanged. No CloakBrowser component is introduced. Chromium remains optional
and installed from the upstream resource only on explicit user request.

Owned-code changes cover low-VRAM model lifetime/offload policy, manual audio
export and source replacement, speaker mapping reuse, OCR cache compatibility,
timeline rendering, SRT export, batch feedback and social queue UI/actions.
These changes do not grant additional rights or remove existing restrictions.
OmniVoice checkpoint remains noncommercial; no model quality reduction or new
checkpoint redistribution is introduced. Hardware tests are mocked policy
tests, not an all-machine 4 GB GPU inference benchmark.

The 0.1.10 ThirdPartySources inventory preserves the exact reviewed FFmpeg
CLI/engine source closures, compiler/CRT inputs, linked-library sources,
Qt/PySide sources, recipes and notices. Its archive must be published alongside
the binaries and verified against runtime/third-party-sources-manifest.json.
Existing application-source, owner-asset and Qt clearances retain their hashes.

Rebuild CPU 12, CUDA 14 and vision 7 with unchanged reviewed dependency locks
and media recipes. Protocol 1/runtime contract 6 are unchanged. Release requires
clean source, strict legal/resource/notices gates, frozen startup, installer
acceptance, full and delta health/rollback/data-preservation tests from 0.1.8
and 0.1.9, and public asset download/hash checks. BUILD-INFO binds final source
provenance. Do not overwrite existing immutable release assets.
