# Speaker timbre and short local speech

## Offline preset-family selection

WeSpeaker embeddings continue to determine speaker identities. Short turns with
very weak matches can form additional, bounded anchor groups instead of being
forced into the only long-speaking actor. The original confident memberships
and expressive-group reconciliation are retained before adding these anchors.

Preset-family selection combines sustained habitual-register evidence with a
small q8 ECAPA voice-profile classifier. It runs on CPU with two inference
threads, using raw mono 16 kHz PCM, and does not import Torch or occupy VRAM.
Independent turn votes avoid a long shout or a single model outlier dominating
the family. Weak/conflicting profiles may inherit a family only with independent
embedding matches and a margin over the opposite family; this never merges
identities or propagates transitively. Unresolved turns use
`omnivoice:female_gentle` (Nữ dịu dàng), not the dormant narrator UI setting.

Source audio is decoded as float PCM: a separated stem can exceed 0 dBFS and
must not be clipped to int16 before embedding/profile inference. Sub-500 ms
turns repeat only their own filter-bank features, as in the
[WeSpeaker extraction implementation](https://raw.githubusercontent.com/wenet-e2e/wespeaker/master/wespeaker/diar/extract_emb.py).
This does not borrow adjacent speech or manufacture confidence. Stable low
register plus a margin over independently supported opposite-family anchors
can retain a short male turn; ambiguous fragments remain marked uncertain.

Compound source captions may contain different speakers. Punctuation and a
measured quiet gap propose boundaries; a large independent profile contrast
AND embedding contrast must corroborate the change. Accepted adjacent turns
cannot collapse into one identity. The source-bound map stores subturns;
translated clauses are synthesized independently and assembled into the same
original caption/voice file. Caption indices, recognition results and OCR are
not repartitioned. Edited captions retain these source mappings; removing the
required clause boundaries produces an actionable error instead of silently
using one wrong voice. Progress counts complete captions, not partial clauses.

An existing local, same-language preview bank supplies conservative child-like
style evidence (multiple references and a margin over adult styles). It avoids
treating every high-pitched child/cartoon voice as an adult female. This is an
acoustic style, not an age estimate. It adds no downloaded model or GPU load.
Automatic male preset rotation no longer arbitrarily selects elderly/mature
voices solely from speaker ordinal. Explicit user preset choices are unchanged.

This predicts an acoustic target preset, **not personal gender identity**. The
underlying human-speech model is not a guarantee for animated, altered, child,
overlapping or noisy voices. Sustained low-register evidence remains important
for the stylized male speech already covered by the expressive-dialogue fix.

The model is checksum-pinned in `core/voice_profile_model.py`. Preparation uses
`scripts/prepare-bundled-voice-profile.py --download`; application inference never
downloads it. Future Core packaging stages both speaker assets. Old Core payloads
without the profile model remain verifiable; missing profile assets use explicit
uncertain/pitch fallback rather than requiring a new GPU resource pack.

Sources and license:

- [Original ECAPA fine-tune](https://huggingface.co/JaesungHuh/voice-gender-classifier).
- [Pinned q8 derivative](https://huggingface.co/Alice-Sabrina-Ivy/voice-gender-classifier-onnx-q8/tree/5debf995242f9851881d4e4848fe6eed2bb5ade7).
- MIT notice: `licenses/ECAPA-Voice-Profile-MIT.txt`.

## Short utterance completeness

For short local phrases, the SDK's reference-calibrated duration estimator is
retained. Do **not** assign a fixed token budget from written word count: this
reproduced decoder collapse into a tone for `Cái này bao nhiêu?` with the male
preset. Reducing the 100 ms edge fade to 5 ms protects final consonants without
forcing generation length. The mixer fits complete speech to the source slot.
Vietnamese bare integers, dot-grouped thousands and decimal-comma numbers are
expanded into spoken words only at synthesis (`90.000` → `chín mươi nghìn`).
Subtitles and reference transcripts are not rewritten. Date/version/identifier
patterns are left alone. No additional model or online service is required.
All local generated speech is checked against the same voice reference's
active-speech RMS: a deficit of more than 3 dB receives one constant gain, capped
at +9 dB and a -1 dB peak ceiling. Pauses/DC are excluded from measurement; nearly
silent residue is never amplified. Normal-level sentences, global user volume,
source/background tracks and Edge audio are untouched. Reference measurement reads at
most 30 seconds once per file state and caches only four scalars, not GPU audio.

The clean-audio v3 policy additionally calibrates requested local clips against
the median of 3–12 ordinary same-speaker sentences already available in that
generation folder. Numbers, tiny reactions and exclamations are not calibration
references. Different speaker IDs/presets and mixed-speaker parent clips are
never pooled. A deficit over 1 dB receives bounded constant gain; louder and
near-target speech is unchanged. Each adjustment is capped at +9 dB with a
−1 dB waveform peak ceiling. Manual regeneration passes the complete provider/preset group as
peer context even when only one changed caption is requested. Existing peer
cache files are read-only, including hard-linked immutable artifacts; only
requested outputs are atomically replaced. Interrupted Auto runs repeat this
idempotent pass before marking the full voice checkpoint complete. Edge and
user mixer-volume settings are untouched.

Metering uses bundled FFmpeg decoding (at most 30 seconds per candidate,
12 candidates per identity), with no model or additional VRAM. Core does not
require SoundFile from an optional AI engine. Full-clip float-PCM peak scans
stream one-second blocks. Only a changed clip requires MP3 encoding.
This remains post-decode level adjustment;
the current OmniVoice model has no guaranteed per-sentence loudness control.

A lightweight audible-span floor detects obvious under-generation even when
silence padding makes a file look long. A lightweight spectral check also rejects
codec DC/tones and repeated narrow-band beeps preceding otherwise valid words.
A stationary, dominant 1–4 kHz ridge lasting at least 320 ms is also rejected
even when sidebands and subsequent valid speech defeat whole-clip checks.
One isolated retry keeps the same voice/reference, changes the latent seed, and
uses a slightly slower natural estimate only for measured under-generation;
it never assigns a fixed duration. CPU inference activity
tracking remains enabled during retries. Failed audio is not published. This is an acoustic safeguard,
not a semantic ASR validator or a promise of perfect pronunciation.

All local voice keys receive the clean-audio policy version; short/numeric keys
also include their text-policy versions. Edge TTS caches remain unchanged.
Multi-speaker clip keys include source timing so a moved caption cannot reuse
another actor's subturn plan. The speaker policy
version changes multi-speaker voice/map keys because their actual voice mapping
changes. Recognition, translation, OCR, separation and aspect caches are not
invalidated. Previously published voice assets remain stored; regenerate voice
explicitly to apply the correction.

## Verification

User source media was inspected read-only, with diagnostics under ignored
`build/` paths. On `nb`, the 19-turn map assigns 17 turns to male presets,
both `You idiot!` (0.78 s) and `Take this!` (0.62 s) to female presets. Sparse
pitch in these short turns requires extremely confident independent timbre
evidence; weakly matched subsecond identities do not displace reliable long-turn
memberships. Confirmed male turns at 3.059 s and 15.226 s are male. On `hnkd`,
the known male turns at 11.184 s and 55.379 s stay male. These are specific
regression observations, not a population accuracy estimate.

Four actual GPU/32-step SDK fixtures (including repeated `Đồ ngốc!` in single
and paired synthesis) were checked with local Whisper Turbo: `Đồ ngốc`,
`Cầm lấy`, `Đồ ngốc`, `Đi mau` were returned completely. Known-preset dialogue
fixtures retain both speaker identities for long and short-only conversations.

The later `nb` beep regression was reproduced with the actual male reference:
fixed duration 1.65 s produced the same tonal artifact as cached sentence 2;
the natural estimator produced complete `Cái này bao nhiêu?`. Raw numeric text
could produce either collapse or two beeps before `90.000 đô la`; explicit
`Chín mươi nghìn đô la` produced complete speech without those artifacts.
All 19 sentences were regenerated through the production worker into isolated
fixtures. Local Whisper Turbo returned all four focused phrases correctly, both
before and after the existing FFmpeg tempo fitter (841/960/780/621 ms slots).
The conservative spectral guard rejected both original bad cached clips and
accepted all 19 corrected clips plus all four fitted clips. Project data/logs
were not rewritten by these diagnostics.
The subsequent quiet-number regression measured sentence 10 at -26.14 dB active
RMS against its male reference at -20.31 dB (nearby male turns -18 to -20 dB).
Reference-based gain restores -20.31 dB with waveform peak 0.377, retaining its
duration and the complete spoken number. This earlier numeric-only correction
is now covered by the all-local clean-audio policy so quiet ordinary dialogue
is protected too.

On the latest `nb` cache, sentence 10 was −20.31 dB before fitting versus
same-speaker ordinary dialogue around −18.7 dB. An isolated partial-regeneration
fixture raises it by 1.62 dB to −18.69 dB; a second pass makes no further change.
After tempo fitting it measures −19.03 dB against the same-speaker median of
−19.04 dB. Local Whisper Turbo returns the complete `90.000 đô la` phrase and
the tone guard accepts the fitted clip.
Original project metadata, logs and cached speech were not rewritten.

For `dc`, the original cached `Cảm ơn cậu` contained a stationary ~2 kHz artifact
before valid speech. The expanded guard rejects this clip but accepts the other
47 original clips and corrected fixtures. Read-only actual-model checks retain
the father's male turns, separate `Đừng làm vậy!` (male) / `Không!` (female),
assign the boy child-like style, retain the following mother's female style,
and give `Mình rất sẵn lòng` / `Cảm ơn cậu` the same male identity. `Dừng lại`
remains explicitly uncertain: no manually supplied annotation was saved.
These are focused regressions, not a guarantee of arbitrary speaker accuracy.
Actual production synthesis generated all 48 parent captions into isolated
fixtures; selected clauses and short phrases were checked before and after
tempo fitting. Waveform guards are not semantic transcription validators.
