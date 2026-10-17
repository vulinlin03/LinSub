# Duration-guided translation

## Recognition group boundaries

Long-source recognition aligns bounded groups of sentences to the audio. Group
intrusion checks compare locally validated word timing with corrected neighbouring
groups, not their provisional proportional Whisper boundaries. If two adjacent
same-language groups overlap, only their two boundary sentences are jointly
aligned once with the already loaded alignment model. The window excludes the
other already aligned neighbours, so a short boundary word cannot lock onto
similar sounds in an earlier/later sentence. Missing words, low scores,
new overlap with other sentences, or failed alignment retain the conservative
fallback; recognized text is never rewritten or removed by this repair.

Recognition timing contract v17 changes the recognition cache key for subsequent
runs. Opening a saved project does not rewrite its subtitles or voice, and OCR,
media, and voice cache contracts are unchanged. Timing repair must be applied by
running recognition again; previously generated voice still follows its saved
subtitle timing until regenerated.

## Translation guidance

HY-MT2 Q4, HY-MT2 full and Gemini receive per-sentence speech duration in the
initial translation request. Prompts ask for concise, natural spoken equivalents
without extra explanation or filler, preserving essential meaning, negation,
names, numbers and tone. A complete word alignment can trim leading/trailing
silence from the guidance; otherwise the original subtitle interval is used.

Known target languages also receive a soft spoken-unit budget. Rates are product
heuristics, not measured TTS durations: Vietnamese 4.5 space-separated syllables/s,
English 2.8 words/s, Chinese 4.5 characters/s, Japanese/Korean 5 characters/s.
Other languages receive duration guidance without a guessed unit budget.
Accuracy takes priority over these approximate targets.

There is **no post-translation shortening request**, no length-triggered retry,
no text truncation and no reduction in generation token limits. Existing bounded
invalid-text/context-leakage recovery and network retries remain. Timestamped
segments, subtitle/voice/OCR caches and published results are not rewritten by
the policy. Translation checkpoints include the policy version so an unfinished
run with an old prompt is not resumed as though it used the new prompt.

The HY-MT2 prompt lives in the External Engine worker. An already installed older
engine will not acquire it merely by editing Core source; the next engine package
must include the updated worker and `translation_timing` module. Existing model
weights, precision and inference configuration are unchanged. Gemini prompts
live in Core. No installed package is modified in place.

`tests/test_translation_timing.py` uses mocked models/network to check duration
mapping, both HY-MT2 paths, Gemini JSON, first-pass-only call counts, invalid
telemetry, recovery and checkpoint preservation. These tests do not establish
translation quality or exact synthesized speech durations; listening tests with
real models remain necessary.

## Gemini response recovery

Flash-Lite 3.1 and 3.5 use minimal thinking with thought summaries disabled.
The parser ignores thought/signature parts, joins final text parts, and accepts
only complete JSON (optionally enclosed in a complete JSON code fence). Cues
are matched by unique integer ID, so a reordered valid answer is safe; missing,
duplicate, unknown or empty cues cannot be published.

Malformed responses retry only the failed batch, within the same three-attempt
budget as transport recovery. Token exhaustion increases the output allowance
up to 8192 on the next attempt. Safety/content blocks do not retry. Successfully
translated batches and resumed cues are retained. Diagnostics include only
allowlisted finish reasons, token counters and retry state, not API keys, raw
responses, thought text or subtitles. This recovery is not a shortening pass.

API references: [GenerateContent response/Part/finishReason](https://ai.google.dev/api/generate-content)
and [GenerateContent thinking levels](https://ai.google.dev/gemini-api/docs/generate-content/thinking).
