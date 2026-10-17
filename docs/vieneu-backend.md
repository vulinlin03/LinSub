# VieNeu voice backend

Manual, Auto and batch projects expose `VieNeu · CPU` (`vieneu`) and
`VieNeu · GPU` (`vieneu-gpu`). One `VieNeu CPU / GPU` resource pack supplies
the checksum-pinned VieNeu 3.8.3 SDK, native phonemizer, v3 Turbo models and
CPU/CUDA codecs. The existing processor pack is selected by the explicit voice
device, not the global recognition/translation device.

VieNeu GPU is first in the backend picker and the default for new Vietnamese
desktop projects in GPU mode. CPU mode defaults to VieNeu CPU; English/Chinese
output uses the existing supported backend. Saved project backend/voice choices
are not migrated to the new default.

Vietnamese/English subtitle text remains selectable with VieNeu; Vietnamese
is its primary quality target, not a guarantee of English pronunciation.
Chinese is blocked in Auto/Batch. Manual checks the published subtitle language,
not the next translation dropdown, and switches VieNeu to the matching
CPU/GPU OmniVoice backend only after publishing Chinese translation successfully.

- CPU: local-only ONNX, fp32, up to six threads. Explicit CPU inference does
  not import Torch even on a CUDA machine. int8 is deliberately not selected
  blindly: upstream warns that CPUs without VNNI may produce distorted speech.
- GPU: local-only PyTorch/CUDA, BF16 on supported cards or FP16 otherwise,
  one chunk/stream at a time. No duplicate speculative warm-up, no second model
  per sentence and no silent switch to CPU. The worker exits after the request
  and releases its CUDA allocation.
- 25 native Vietnamese presets. Speaker identification reuses the existing
  source map and converts target presets in memory, without rewriting source
  identity caches. Manual's `per-speaker` cache identity is not a preset name;
  it is validated only after each source speaker receives its native target voice.
- Voice cloning uses `vieneu:clone` on either CPU or GPU, through the existing
  authorised sample import/recording dialog in Manual, Auto and batch settings.
  Samples are decoded once per request to a temporary mono 48 kHz WAV (at most
  eight seconds), then enrolled/denoised by the SDK. The SDK reuses its reference
  embedding/codes for subsequent sentences in that worker. No reference
  transcript, Whisper model, automatic source clone or default preset fallback
  is used. A clear 3–8 second single-speaker sample is recommended. Replacing the
  reference participates in existing per-clip cache signatures. Clone and
  automatic multiple-speaker assignment are separate modes.
- All 25 presets have packaged, sentence-locked Vietnamese MP3 previews.
  CPU/GPU selections share the exact same sample; runtime preview does not
  load VieNeu, require a model/resource pack or make a network request.
  `scripts/generate-vieneu-voice-samples.py` regenerates assets using an
  explicit existing model root and isolated temporary runtime data. It prints
  catalog records for review instead of rewriting the shared sample manifest.
- Output currently limited to Vietnamese; unsupported language selections are
  blocked instead of silently substituting OmniVoice. Source language may differ.
- Progress advances only after validated audio has been atomically encoded.
  Pause/cancel uses the shared registered-process termination mechanism, including
  while loading/inferencing. Completed sentence caches use provider-specific keys.
- No automatic model download during inference, injected expression cues,
  reference dialogue or audio watermark. Basic waveform/tone safeguards precede
  publication. Generative pronunciation still requires listening tests.

Public/frozen builds need CPU/CUDA processor **contract 7**, including the
`vieneu_worker` entrypoint. An older engine is not accepted as a compatible
VieNeu runtime. The new resource specification downloads pinned official model
assets directly (~1.00 GB decimal), not an invented release archive. Engines
must be rebuilt at the next authorized release; this change itself does not
build or publish anything.

Existing processors retain their baseline contract for other models; adding
VieNeu does not invalidate all older recognition/separation engines. In source
checkouts, a separate existing `.venv` AI interpreter can serve VieNeu while
the desktop runs in `.venv-core`. No inference dependency is installed in Core.

Source validation used actual local weights for CPU and CUDA synthesis, including
short utterances and numbers. CPU status confirmed that Torch was not imported.
The CUDA run on an RTX 4060 8 GB, with its Torch allocator capped at 2.5 GiB,
reported approximately 0.464 GiB peak allocated / 0.621 GiB peak reserved for
the tested request. These are allocator measurements, not total driver VRAM,
and do not replace a physical RTX 2050 4 GB compatibility test. No public
processor archive was built during this validation.

Additional actual-weight checks passed for clone and per-speaker preset synthesis
on CPU and CUDA, with the same 2.5 GiB Torch allocation cap. The speaker mappings
in these synthesis checks were supplied fixtures, not an accuracy evaluation of
the source speaker classifier.

Upstream references: [SDK installation/backends](https://docs.vieneu.io/docs/sdk/standard-mode/),
[VieNeu source](https://github.com/pnnbao97/VieNeu-TTS),
[pinned Turbo weights](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo/tree/61b85e3d937fbbacb387714180e8182823512523).
