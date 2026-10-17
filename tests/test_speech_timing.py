import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linsub.pipeline.speech_timing import voiced_subtitle_segments, fitted_speech_duration_ms
from linsub.pipeline.sequence_compiler import write_subtitles
from linsub.schemas.editor import EditorDocument, EditorTrack, EditorClip, EditorAsset


class SpeechTimingTests(unittest.TestCase):
    def test_short_voice_leaves_pause_without_mutating_editable_timing(self):
        segments = [{"start": 1, "end": 5.69, "text": "Câu thứ nhất"},
                    {"start": 7, "end": 12.65, "text": "Câu thứ hai"}]
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "voice.wav"
            audio.touch()
            with patch("linsub.pipeline.speech_timing._trimmed_duration", side_effect=[1930, 2600]):
                result = voiced_subtitle_segments(segments, [str(audio), str(audio)])
        self.assertAlmostEqual(result[0]["end"], 2.93)
        self.assertAlmostEqual(result[1]["end"], 9.60)
        self.assertEqual(segments[0]["end"], 5.69)
        self.assertEqual(result[1]["start"], 7)

    def test_overrun_uses_same_safety_margin_as_audio_export(self):
        self.assertEqual(fitted_speech_duration_ms(3610, 2630), 2610)
        self.assertEqual(fitted_speech_duration_ms(1930, 4690), 1930)
        self.assertEqual(fitted_speech_duration_ms(2630, 2630), 2630)

    def test_no_voice_preserves_source_caption_timing(self):
        segments = [{"start": 1, "end": 5, "text": "Test"}]
        self.assertEqual(voiced_subtitle_segments(segments, [""]), segments)

    def test_export_follows_voice_but_muted_voice_preserves_source_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "voice.wav"
            audio.touch()
            document = EditorDocument(video_id="test", tracks=[
                EditorTrack(track_id="subtitles", kind="subtitle", name="Subtitles"),
                EditorTrack(track_id="voice", kind="voice", name="Voice")],
                clips=[EditorClip(clip_id="s", track_id="subtitles", kind="subtitle",
                                  segment_id="1", name="Test", start_ms=1000, duration_ms=4690),
                       EditorClip(clip_id="v", track_id="voice", kind="voice",
                                  segment_id="1", name="Test", asset_id="a", start_ms=1000,
                                  duration_ms=4690)],
                assets=[EditorAsset(asset_id="a", kind="audio", path=str(audio))])
            output = Path(folder) / "captions.srt"
            with patch("linsub.pipeline.speech_timing._trimmed_duration", return_value=1930):
                self.assertTrue(write_subtitles(document, output))
                self.assertIn("00:00:01,000 --> 00:00:02,930", output.read_text())
                document.tracks[1].muted = True
                write_subtitles(document, output)
            self.assertEqual(document.clips[0].duration_ms, 4690)

    def test_calculate_speech_fitting_window_natural_pacing(self):
        from linsub.pipeline.audio_timeline import calculate_speech_fitting_window

        # 1. Standard fit within source speech window
        target, speed = calculate_speech_fitting_window(1000, 2200, 3000, 10000, 1200)
        self.assertEqual(target, 1200)
        self.assertEqual(speed, 1.0)

        # 2. Slight overrun into trailing pause: plays at 1.0x natural speed instead of rushing
        target, speed = calculate_speech_fitting_window(1000, 2200, 3500, 10000, 1500)
        self.assertEqual(target, 1500)
        self.assertEqual(speed, 1.0)

        # 3. Fast dialogue with narrow gap: fits within available window without overlapping next line
        target, speed = calculate_speech_fitting_window(1000, 2200, 2300, 10000, 1500)
        self.assertLessEqual(target, 2300 - 1000 - 60)
        self.assertGreater(speed, 1.0)
        self.assertLessEqual(speed, 1.30)

        # 4. Very short utterance in long 3.5s slot: receives subtle natural relaxation
        target, speed = calculate_speech_fitting_window(1000, 4500, 5000, 10000, 1200)
        self.assertGreater(target, 1200)
        self.assertAlmostEqual(speed, 0.94, places=2)

    def test_preprocess_text_for_tts_cadence_and_pausing(self):
        from linsub.pipeline.tts import preprocess_text_for_tts, is_clause_continuation

        # Natural breath commas inserted before conjunctions
        raw = "Tôi đã cố gắng rất nhiều nhưng kết quả không như ý"
        processed = preprocess_text_for_tts(raw)
        self.assertEqual(processed, "Tôi đã cố gắng rất nhiều, nhưng kết quả không như ý.")

        # Clause continuation ends with comma instead of abrupt period
        clause = "Khi tôi bước vào căn phòng đó"
        cont_processed = preprocess_text_for_tts(clause, is_clause_continuation=True)
        self.assertEqual(cont_processed, "Khi tôi bước vào căn phòng đó,")

        # Standalone complete sentence ends with period
        sentence = "Hôm nay thời tiết rất đẹp"
        sent_processed = preprocess_text_for_tts(sentence, is_clause_continuation=False)
        self.assertEqual(sent_processed, "Hôm nay thời tiết rất đẹp.")

    def test_is_clause_continuation_detection(self):
        from linsub.pipeline.tts import is_clause_continuation

        # Lowercase next segment -> continuing clause
        self.assertTrue(is_clause_continuation("Khi tôi đến nơi", "thì mọi chuyện đã kết thúc"))
        # Ending in open conjunction/connective word -> continuing clause
        self.assertTrue(is_clause_continuation("Tôi nghĩ rằng", "Chúng ta nên đi"))
        # Complete sentence followed by another -> False
        self.assertFalse(is_clause_continuation("Tôi đi chợ.", "Tôi mua cá"))
        # Two distinct statements starting with capital -> False
        self.assertFalse(is_clause_continuation("Xin chào", "Thế giới"))
