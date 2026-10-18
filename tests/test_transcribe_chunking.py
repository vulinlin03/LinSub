import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import numpy as np

# Mock heavy DL dependencies if running in lightweight environment
sys.modules.setdefault("torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)))
sys.modules.setdefault("torchaudio", SimpleNamespace())
sys.modules.setdefault("whisperx", SimpleNamespace(audio=SimpleNamespace(subprocess=None)))
sys.modules.setdefault("whisperx.audio", SimpleNamespace(subprocess=None))

from linsub.pipeline.transcribe import (
    _format_time,
    _find_optimal_audio_split,
    _calculate_audio_chunks,
    _transcribe_chunks,
    _AUDIO_SAMPLE_RATE,
)


class TestTranscribeChunking(unittest.TestCase):
    def test_format_time(self):
        self.assertEqual(_format_time(0), "00:00")
        self.assertEqual(_format_time(45), "00:45")
        self.assertEqual(_format_time(600), "10:00")
        self.assertEqual(_format_time(3600), "01:00:00")
        self.assertEqual(_format_time(11396), "03:09:56")

    def test_find_optimal_audio_split(self):
        sr = _AUDIO_SAMPLE_RATE
        # 30 seconds of audio with loud noise except a 2-second silence at 15s
        audio = np.ones(30 * sr, dtype=np.float32)
        # silence at 14s-16s
        audio[14 * sr : 16 * sr] = 0.0

        split = _find_optimal_audio_split(audio, target_sample=15 * sr, search_half_window_sec=5.0)
        # Split point should be within the silent region [14s, 16s]
        self.assertGreaterEqual(split, 14 * sr)
        self.assertLessEqual(split, 16 * sr)

    def test_calculate_audio_chunks_short_audio(self):
        sr = _AUDIO_SAMPLE_RATE
        # 5 minutes audio (less than 12m threshold)
        audio = np.zeros(300 * sr, dtype=np.float32)
        chunks = _calculate_audio_chunks(audio, target_chunk_sec=600.0)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (0, 300 * sr))

    def test_calculate_audio_chunks_long_audio_continuity(self):
        sr = _AUDIO_SAMPLE_RATE
        # 35 minutes of audio
        total_samples = 35 * 60 * sr
        audio = np.zeros(total_samples, dtype=np.float32)
        chunks = _calculate_audio_chunks(audio, target_chunk_sec=600.0)

        # Chunks must cover start to end continuously without gaps
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[-1][1], total_samples)

        for i in range(len(chunks) - 1):
            # End of chunk i must equal start of chunk i + 1
            self.assertEqual(chunks[i][1], chunks[i + 1][0])

    def test_transcribe_chunks_timestamp_shifting_and_checkpoint(self):
        sr = _AUDIO_SAMPLE_RATE
        # 25 minutes audio: should create ~2-3 chunks of 10m
        total_samples = 25 * 60 * sr
        audio = np.zeros(total_samples, dtype=np.float32)

        calls = []

        def fake_vad_transcribe(asr_model, chunk_audio, profile, device, video_id):
            idx = len(calls)
            calls.append(idx)
            return {
                "segments": [
                    {"start": 1.0, "end": 4.0, "text": f"Phrase in chunk {idx}"}
                ],
                "language": "vi",
            }

        with tempfile.TemporaryDirectory() as td:
            ckpt_path = Path(td) / "progress.json"

            with patch("linsub.pipeline.transcribe._transcribe_with_vad_recovery", side_effect=fake_vad_transcribe):
                profile = MagicMock(whisper_batch_size=1)
                result = _transcribe_chunks(
                    asr_model=None,
                    audio=audio,
                    profile=profile,
                    device="cpu",
                    video_id="test-vid",
                    checkpoint_path=str(ckpt_path),
                    checkpoint_key="key123",
                )

            # Check that segments were shifted appropriately and merged
            self.assertGreaterEqual(len(result["segments"]), 2)
            self.assertEqual(result["language"], "vi")
            self.assertAlmostEqual(result["segments"][0]["start"], 1.0, places=1)
            self.assertGreater(result["segments"][1]["start"], 500.0)

            # Ensure all segments are strictly sorted by start
            starts = [s["start"] for s in result["segments"]]
            self.assertEqual(starts, sorted(starts))

    def test_transcribe_chunks_resumes_from_checkpoint(self):
        sr = _AUDIO_SAMPLE_RATE
        total_samples = 25 * 60 * sr
        audio = np.zeros(total_samples, dtype=np.float32)

        with tempfile.TemporaryDirectory() as td:
            ckpt_path = Path(td) / "progress.json"
            chunk_file = Path(td) / "recognition-chunks.json"

            import json
            chunk_file.write_text(json.dumps({
                "key": "key123",
                "chunks": [
                    {
                        "index": 0,
                        "start_time": 0.0,
                        "end_time": 600.0,
                        "segments": [{"start": 2.0, "end": 5.0, "text": "Pre-existing chunk 0 segment"}],
                        "language": "vi"
                    }
                ],
                "detected_languages": ["vi"]
            }))

            calls = []

            def fake_vad_transcribe(asr_model, chunk_audio, profile, device, video_id):
                calls.append(len(calls))
                return {
                    "segments": [
                        {"start": 1.0, "end": 3.0, "text": f"New segment"}
                    ],
                    "language": "vi",
                }

            with patch("linsub.pipeline.transcribe._transcribe_with_vad_recovery", side_effect=fake_vad_transcribe):
                profile = MagicMock(whisper_batch_size=1)
                result = _transcribe_chunks(
                    asr_model=None,
                    audio=audio,
                    profile=profile,
                    device="cpu",
                    video_id="test-vid",
                    checkpoint_path=str(ckpt_path),
                    checkpoint_key="key123",
                )

            chunks = _calculate_audio_chunks(audio, target_chunk_sec=600.0)
            self.assertEqual(len(calls), len(chunks) - 1)
            self.assertEqual(result["segments"][0]["text"], "Pre-existing chunk 0 segment")
            self.assertEqual(result["segments"][0]["start"], 2.0)
            self.assertFalse(chunk_file.exists())


if __name__ == "__main__":
    unittest.main()

