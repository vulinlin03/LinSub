# LinSub 1.0.0 — scoped technical packaging review

Reviewer: Codex. Date: 2026-10-13 (Asia/Saigon). This is a technical scope
and packaging review, not independent legal, ownership or patent certification.
The owner explicitly requested commit/push, build and publication of 1.0.0,
updates from 0.1.8 onward, and preservation of all existing releases.

Owned changes cover preview/timeline controls, vertical framing, Edge TTS
integration using the already pinned dependency, IME/focus/dropdown behavior,
session notices, export progress, social queue order, speaker timbre/subturn
mapping and local TTS natural-duration/number/quality/peer-level policies.
No new vendor artwork, fonts or paid services are introduced. Existing model,
media, browser and dependency lock pins are preserved. A small checksum-pinned
ECAPA q8 acoustic voice-profile model is added to Core; its derivative and
original project identify MIT licensing, retained in
licenses/ECAPA-Voice-Profile-MIT.txt. Acoustic style is not personal gender/age
identity. Automatic classification remains uncertain for some short, distorted
or overlapping speech. No population-accuracy claim is made.

OmniVoice checkpoint remains noncommercial and separately downloaded. Target
voice references are the existing owner-attested preview assets; the source
media used in diagnostics is not redistributed. Preset inference preserves
model weights/steps; 4 GB VRAM policies do not claim an all-GPU benchmark.
The lightweight Core uses bundled FFmpeg and float PCM for audio metering;
SoundFile/Torch remain optional engine dependencies, not new Core libraries.

ThirdPartySources preserves the exact reviewed FFmpeg CLI/engine closures,
Qt/PySide sources, compiler inputs, library sources, recipes and notices.
The new source asset adds the retained model license, not a changed library
recipe. Existing application-source, owner-asset and Qt clearances retain
their hashes and restrictions. No license blockers are removed or waived.

Rebuild CPU 13 and CUDA 15 with the unchanged reviewed dependency locks and
media recipes. OCR/vision 7 stays at its immutable 0.1.10 URL; preserving old
releases is a prerequisite for this pin. Protocol 1/runtime contract 6 and
data compatibility remain unchanged. Final BUILD-INFO binds clean source.
Require strict legal/resource/notices gates, frozen startup, installer smoke,
Full/Delta health/rollback/data-preservation checks from 0.1.8/0.1.9/0.1.10 and
public asset download/hash checks before declaring delivery complete.
