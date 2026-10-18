import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from linsub.services.speaker_profiles import load_video_speakers, classify_gender
from linsub.pipeline.process_video import _voice_checkpoint_signature


class SpeakerVoiceMappingTests(unittest.TestCase):
    def test_classify_gender(self):
        # High pitch indicates female
        self.assertEqual(classify_gender(pitch_hz=260.0), "female")
        self.assertEqual(classify_gender(pitch_hz=110.0), "male")
        self.assertEqual(classify_gender(pitch_hz=0.0), "male")
        self.assertEqual(classify_gender(timbre_style="female_soft"), "female")
        self.assertEqual(classify_gender(timbre_style="male_deep"), "male")

    def test_load_video_speakers_with_overrides(self):
        import json
        video = SimpleNamespace(
            video_id="test-vid-123",
            tts_provider="vieneu",
            files={
                "video_input": "C:/fake/path/input/test.mp4",
                "speaker_voice_overrides": {
                    "speaker-1": "vieneu:Minh Đức",
                    "speaker-2": "vieneu:Ngọc Huyền"
                }
            }
        )

        mock_segments = [
            {
                "speaker_id": "speaker-1",
                "speaker_voice": "vieneu:Hải Đăng",
                "speaker_pitch_hz": 120.5,
                "text": "Xin chào mọi người.",
                "speaker_turns": []
            },
            {
                "speaker_id": "speaker-2",
                "speaker_voice": "vieneu:Trúc Ly",
                "speaker_pitch_hz": 245.0,
                "text": "Chào bạn, hôm nay thế nào?",
                "speaker_turns": []
            },
            {
                "speaker_id": "speaker-1",
                "speaker_voice": "vieneu:Hải Đăng",
                "speaker_pitch_hz": 118.0,
                "text": "Tôi rất vui được gặp lại.",
                "speaker_turns": []
            }
        ]

        mock_file = mock.MagicMock()
        mock_file.read_text.return_value = json.dumps(mock_segments)

        with mock.patch("pathlib.Path.is_dir", return_value=True), \
             mock.patch("pathlib.Path.glob", return_value=[mock_file]):
            profiles = load_video_speakers(video, "vieneu")

        self.assertEqual(len(profiles), 2)
        p1 = next(p for p in profiles if p["speakerId"] == "speaker-1")
        self.assertEqual(p1["assignedVoice"], "vieneu:Minh Đức")
        self.assertEqual(p1["defaultVoice"], "vieneu:Hải Đăng")
        self.assertEqual(p1["turnCount"], 2)
        self.assertEqual(p1["gender"], "male")
        self.assertEqual(p1["sampleText"], "Xin chào mọi người.")

        p2 = next(p for p in profiles if p["speakerId"] == "speaker-2")
        self.assertEqual(p2["assignedVoice"], "vieneu:Ngọc Huyền")
        self.assertEqual(p2["defaultVoice"], "vieneu:Trúc Ly")
        self.assertEqual(p2["turnCount"], 1)
        self.assertEqual(p2["gender"], "female")
        self.assertEqual(p2["sampleText"], "Chào bạn, hôm nay thế nào?")

    def test_voice_checkpoint_signature_changes_with_speaker_overrides(self):
        video_base = SimpleNamespace(
            video_id="vid-1",
            tts_provider="vieneu",
            tts_voice="vieneu:Minh Đức",
            tts_volume=100,
            speaker_mode="multiple",
            target_language="vi",
            project_type="auto",
            manual_target_tool="voice",
            files={"speaker_voice_overrides": {"speaker-1": "vieneu:Quang Sơn"}}
        )
        sig1 = _voice_checkpoint_signature(video_base, "transcript-v1")

        video_modified = SimpleNamespace(
            video_id="vid-1",
            tts_provider="vieneu",
            tts_voice="vieneu:Minh Đức",
            tts_volume=100,
            speaker_mode="multiple",
            target_language="vi",
            project_type="auto",
            manual_target_tool="voice",
            files={"speaker_voice_overrides": {"speaker-1": "vieneu:Thái Sơn"}}
        )
        sig2 = _voice_checkpoint_signature(video_modified, "transcript-v1")
        self.assertNotEqual(sig1, sig2, "Changing character voice overrides must invalidate the cache signature")

    def test_generate_voice_parts_respects_speaker_overrides(self):
        import tempfile
        from linsub.pipeline import tts, omnivoice_tts

        synthesized_voices = []

        def synthesize_batch(items, video_id, *, language_id, speaker_mode="single", progress_callback=None, device="cpu"):
            for item in items:
                synthesized_voices.append(item["voice"])
                Path(item["output_path"]).write_bytes(b"\xff\xf3\x64" + b"\x00" * 700)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            segments_path = temp_path / "segments.json"
            source_segments_path = temp_path / "source_segments.json"
            voice_dir = temp_path / "voice"

            segments_data = [
                {"text": "Câu nói của nhân vật một", "start": 0.0, "end": 2.0},
                {"text": "Câu nói của nhân vật hai", "start": 2.5, "end": 4.5}
            ]
            source_segments_data = [
                {
                    "speaker_id": "speaker-1",
                    "speaker_voice": "omnivoice:male",
                    "start": 0.0,
                    "end": 2.0,
                    "speaker_turns": []
                },
                {
                    "speaker_id": "speaker-2",
                    "speaker_voice": "omnivoice:female",
                    "start": 2.5,
                    "end": 4.5,
                    "speaker_turns": []
                }
            ]
            segments_path.write_text(json.dumps(segments_data), encoding="utf-8")
            source_segments_path.write_text(json.dumps(source_segments_data), encoding="utf-8")

            dummy_audio = temp_path / "audio.wav"
            dummy_audio.write_bytes(b"RIFF" + bytes(256))

            mock_video = SimpleNamespace(
                video_id="vid-multi-test",
                tts_provider="omnivoice",
                tts_voice="omnivoice:female",
                speaker_mode="multiple",
                files={
                    "source_segments": str(source_segments_path),
                    "speech_audio": str(dummy_audio),
                    "speaker_voice_overrides": {
                        "speaker-1": "omnivoice:boy",
                        "speaker-2": "omnivoice:storyteller"
                    }
                }
            )

            with (
                mock.patch.object(omnivoice_tts, "synthesize_batch_to_mp3", side_effect=synthesize_batch),
                mock.patch.object(omnivoice_tts, "runtime_description", return_value="cpu worker"),
                mock.patch("linsub.pipeline.speaker_identity.prepare_speakers", return_value=source_segments_data),
                mock.patch("linsub.pipeline.speaker_identity._valid_map", return_value=True),
                mock.patch.object(tts, "log_to_video"),
                mock.patch.object(tts, "get_video", return_value=mock_video)
            ):
                tts.generate_voice_parts(
                    str(segments_path),
                    str(voice_dir),
                    "omnivoice:multiple",
                    "vid-multi-test",
                    provider="omnivoice"
                )

            # Both segments should have received the overridden voices, not the defaults
            self.assertEqual(synthesized_voices, ["omnivoice:boy", "omnivoice:storyteller"])

    def test_generate_voice_parts_respects_speaker_overrides_vieneu(self):
        import tempfile
        from linsub.pipeline import tts, vieneu_tts

        synthesized_voices = []

        def synthesize_batch(items, video_id, *, language_id, speaker_mode="single", progress_callback=None, device="cpu"):
            for item in items:
                synthesized_voices.append(item["voice"])
                Path(item["output_path"]).write_bytes(b"\xff\xf3\x64" + b"\x00" * 700)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            segments_path = temp_path / "segments.json"
            source_segments_path = temp_path / "source_segments.json"
            voice_dir = temp_path / "voice"

            segments_data = [
                {"text": "Chào bạn nhé", "start": 0.0, "end": 2.0},
                {"text": "Hẹn gặp lại bạn", "start": 2.5, "end": 4.5}
            ]
            source_segments_data = [
                {
                    "speaker_id": "speaker-1",
                    "speaker_voice": "vieneu:Hải Đăng",
                    "start": 0.0,
                    "end": 2.0,
                    "speaker_turns": []
                },
                {
                    "speaker_id": "speaker-2",
                    "speaker_voice": "vieneu:Trúc Ly",
                    "start": 2.5,
                    "end": 4.5,
                    "speaker_turns": []
                }
            ]
            segments_path.write_text(json.dumps(segments_data), encoding="utf-8")
            source_segments_path.write_text(json.dumps(source_segments_data), encoding="utf-8")

            dummy_audio = temp_path / "audio.wav"
            dummy_audio.write_bytes(b"RIFF" + bytes(256))

            mock_video = SimpleNamespace(
                video_id="vid-vieneu-test",
                tts_provider="vieneu",
                tts_voice="vieneu:Minh Đức",
                speaker_mode="multiple",
                files={
                    "source_segments": str(source_segments_path),
                    "speech_audio": str(dummy_audio),
                    "speaker_voice_overrides": {
                        "speaker-1": "vieneu:Quang Sơn",
                        "speaker-2": "vieneu:Ngọc Huyền"
                    }
                }
            )

            with (
                mock.patch.object(vieneu_tts, "synthesize_batch_to_mp3", side_effect=synthesize_batch),
                mock.patch("linsub.pipeline.speaker_identity.prepare_speakers", return_value=source_segments_data),
                mock.patch("linsub.pipeline.speaker_identity._valid_map", return_value=True),
                mock.patch.object(tts, "log_to_video"),
                mock.patch.object(tts, "get_video", return_value=mock_video)
            ):
                tts.generate_voice_parts(
                    str(segments_path),
                    str(voice_dir),
                    "vieneu:Minh Đức",
                    "vid-vieneu-test",
                    provider="vieneu"
                )

            self.assertEqual(synthesized_voices, ["vieneu:Quang Sơn", "vieneu:Ngọc Huyền"])

    def test_generate_voice_parts_respects_male_female_overrides(self):
        import tempfile
        from linsub.pipeline import tts, vieneu_tts

        synthesized_voices = []

        def synthesize_batch(items, video_id, *, language_id, speaker_mode="single", progress_callback=None, device="cpu"):
            for item in items:
                synthesized_voices.append(item["voice"])
                Path(item["output_path"]).write_bytes(b"\xff\xf3\x64" + b"\x00" * 700)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            segments_path = temp_path / "segments.json"
            source_segments_path = temp_path / "source_segments.json"
            voice_dir = temp_path / "voice"

            segments_data = [
                {"text": "Nam 1 nói", "start": 0.0, "end": 2.0},
                {"text": "Nữ nói", "start": 2.5, "end": 4.5},
                {"text": "Nam 2 nói", "start": 5.0, "end": 7.0}
            ]
            source_segments_data = [
                {
                    "speaker_id": "speaker-1",
                    "speaker_voice": "vieneu:Hải Đăng",
                    "speaker_timbre_style": "male",
                    "speaker_pitch_hz": 120.0,
                    "start": 0.0,
                    "end": 2.0,
                    "speaker_turns": []
                },
                {
                    "speaker_id": "speaker-2",
                    "speaker_voice": "vieneu:Trúc Ly",
                    "speaker_timbre_style": "female",
                    "speaker_pitch_hz": 230.0,
                    "start": 2.5,
                    "end": 4.5,
                    "speaker_turns": []
                },
                {
                    "speaker_id": "speaker-3",
                    "speaker_voice": "vieneu:Hải Đăng",
                    "speaker_timbre_style": "male",
                    "speaker_pitch_hz": 115.0,
                    "start": 5.0,
                    "end": 7.0,
                    "speaker_turns": []
                }
            ]
            segments_path.write_text(json.dumps(segments_data), encoding="utf-8")
            source_segments_path.write_text(json.dumps(source_segments_data), encoding="utf-8")

            dummy_audio = temp_path / "audio.wav"
            dummy_audio.write_bytes(b"RIFF" + bytes(256))

            mock_video = SimpleNamespace(
                video_id="vid-mf-test",
                tts_provider="vieneu",
                tts_voice="vieneu:Minh Đức",
                speaker_mode="multiple",
                files={
                    "source_segments": str(source_segments_path),
                    "speech_audio": str(dummy_audio),
                    "speaker_voice_overrides": {
                        "male": "vieneu:Quang Sơn",
                        "female": "vieneu:Thu Uyên"
                    }
                }
            )

            with (
                mock.patch.object(vieneu_tts, "synthesize_batch_to_mp3", side_effect=synthesize_batch),
                mock.patch("linsub.pipeline.speaker_identity.prepare_speakers", return_value=source_segments_data),
                mock.patch("linsub.pipeline.speaker_identity._valid_map", return_value=True),
                mock.patch.object(tts, "log_to_video"),
                mock.patch.object(tts, "get_video", return_value=mock_video)
            ):
                tts.generate_voice_parts(
                    str(segments_path),
                    str(voice_dir),
                    "vieneu:Minh Đức",
                    "vid-mf-test",
                    provider="vieneu"
                )

            # All male characters speak Quang Sơn, female speaks Thu Uyên
            self.assertEqual(synthesized_voices, ["vieneu:Quang Sơn", "vieneu:Thu Uyên", "vieneu:Quang Sơn"])


if __name__ == "__main__":
    unittest.main()

