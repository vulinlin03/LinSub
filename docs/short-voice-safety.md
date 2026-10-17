# OmniVoice generation: legacy conditioning and fade protection

The production synthesis path uses the same preset/reference conditioning,
raw text, seed, SDK duration estimator, speed and padding as v0.1.10. The only
generation override is `fade_duration=0.0` for all OmniVoice utterances: the SDK's
100 ms fade can remove audible final consonants from tiny reactions. No target/source
subtitle duration is passed to the generative model.

The temporary auxiliary Whisper content verifier, generated/borrowed compact
references and explicit linear duration calibration were removed. They made
short dialogue much slower and introduced false failures, including Vietnamese
ASR writing a spoken number as digits. Translated names and borrowed words are
trusted input, not corrected or checked against a guessed ASR spelling. Normal
GPU batching is no longer disabled by a mandatory sentence-by-sentence ASR gate.

Existing lightweight finite/silence/FFT codec-collapse checks remain before
MP3/cache publication. Only clear acoustic failure allows one bounded retry,
using the same preset/reference and unchanged speed/duration; there is no ASR
retry, substitute speaker or reference-generation loop. These checks are not a
guarantee of pronunciation or speaker-identification accuracy.

Source speaker mapping/subturns and the optional Edge/VieNeu backends are
independent of this rollback. Per-speaker loudness handling remains after
synthesis; it does not change the decoder's conditioning or create phonemes.
Manual/Auto OmniVoice policy keys change so clips created with the previous
fade/generation settings are not mistaken for current results. Subtitle,
OCR and other provider caches are unchanged. Opening a project does not modify
its audio or source mapping. No build/publication accompanies this source fix.
