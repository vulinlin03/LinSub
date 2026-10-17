"""Real disk checkpoints with fake inference; never touch user projects."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from linsub.pipeline import transcribe as asr
from linsub.services.recognition_progress import RecognitionProgress


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"test audio")
    output = tmp_path / "staging" / "segments.json"
    progress = tmp_path / "recognition-progress.json"
    profile = SimpleNamespace(cuda_available=False, cpu_threads=2, whisper_batch_size=1)
    monkeypatch.setattr(asr, "runtime_profile", lambda: profile)
    monkeypatch.setattr("linsub.core.hardware.cpu_runtime_profile", lambda value, **_: value)
    monkeypatch.setattr(asr, "_WARM_ASR_MODEL", None)
    monkeypatch.setattr(asr, "_load_whisper_model", Mock(return_value=object()))
    monkeypatch.setattr(asr, "_constrain_four_gb_vad", Mock())
    monkeypatch.setattr(asr.whisperx, "load_audio", Mock(return_value=np.zeros(32000)))
    monkeypatch.setattr(asr, "_release_cuda", Mock())
    monkeypatch.setattr(asr, "log_to_video", Mock())
    monkeypatch.setattr(asr, "check_cancellation", Mock())
    row = {"start": 0.1, "end": 1.5, "text": "Hello friend.", "language": "en", "language_confidence": 1.0}
    decode = Mock(return_value={"language": "en", "segments": [dict(row)]})
    align = Mock(side_effect=lambda _audio, rows, *_args, **_kwargs: rows)
    detect = Mock(side_effect=lambda _model, _audio, rows, *_args: rows)
    monkeypatch.setattr(asr, "_transcribe_with_vad_recovery", decode)
    monkeypatch.setattr(asr, "_align_segments_by_language", align)
    monkeypatch.setattr(asr, "_detect_segment_languages", detect)
    monkeypatch.setattr(asr, "_retranscribe_mixed_language_segments", lambda _model, _audio, rows, *_args: rows)
    monkeypatch.setattr(asr, "_refine_suspicious_sentence_timing", Mock())
    def run(callback=None, key="generation-1"):
        return asr.transcribe(str(audio), str(output), "auto", "fixture", progress_callback=callback,
                              checkpoint_path=str(progress), checkpoint_key=key)
    return SimpleNamespace(run=run, audio=audio, output=output, progress=progress,
                           decode=decode, align=align, detect=detect)


@pytest.mark.parametrize("event,stage,align_calls", [
    ("transcribed", "transcribed", 1),
    ("segmenting", "aligned", 1),
    ("detecting_languages", "complete", 1),
    ("saved", "complete", 1),
])
def test_pause_resumes_completed_phases_after_new_staging(runtime, event, stage, align_calls):
    def pause(current, _detail):
        if current == event:
            raise RuntimeError("paused")
    with pytest.raises(RuntimeError, match="paused"):
        runtime.run(pause)
    assert json.loads(runtime.progress.read_text())["payload"]["stage"] == stage
    runtime.output.unlink(missing_ok=True)  # discarded Manual staging
    rows, language = runtime.run()
    assert language == "en" and rows[0]["text"] == "Hello friend."
    assert runtime.decode.call_count == 1
    assert runtime.align.call_count == align_calls
    assert runtime.detect.call_count == 1
    assert json.loads(runtime.output.read_text()) == rows


def test_pause_during_language_validation_keeps_aligned_sentences(runtime):
    runtime.detect.side_effect = RuntimeError("paused")
    with pytest.raises(RuntimeError, match="paused"):
        runtime.run()
    assert json.loads(runtime.progress.read_text())["payload"]["stage"] == "aligned"
    runtime.detect.side_effect = lambda _model, _audio, rows, *_args: rows
    runtime.run()
    assert runtime.decode.call_count == 1
    assert runtime.align.call_count == 1
    assert runtime.detect.call_count == 2


def test_complete_resume_does_not_load_any_model_or_audio(runtime, monkeypatch):
    runtime.run()
    loader, audio = Mock(side_effect=AssertionError("model loaded")), Mock(side_effect=AssertionError("audio loaded"))
    monkeypatch.setattr(asr, "_load_whisper_model", loader)
    monkeypatch.setattr(asr.whisperx, "load_audio", audio)
    runtime.output.unlink()
    runtime.run()
    loader.assert_not_called()
    audio.assert_not_called()


@pytest.mark.parametrize("change", ["audio", "generation", "corrupt"])
def test_changed_input_restart_or_corruption_never_reuses_checkpoint(runtime, change):
    runtime.run()
    if change == "audio":
        runtime.audio.write_bytes(b"different source")
    elif change == "corrupt":
        saved = json.loads(runtime.progress.read_text())
        saved["payload"]["segments"][0]["text"] = "modified"
        runtime.progress.write_text(json.dumps(saved))
    runtime.run(key="generation-2" if change == "generation" else "generation-1")
    assert runtime.decode.call_count == 2
    assert runtime.align.call_count == 2


def test_checkpoint_model_language_identity_and_invalid_rows(runtime):
    checkpoint = RecognitionProgress(runtime.progress, str(runtime.audio), model="small", language="auto", key="one")
    checkpoint.save("aligned", [{"start": 0, "end": 1, "text": "Hello"}], "en")
    for kwargs in ({"model": "large-v3-turbo", "language": "auto", "key": "one"},
                   {"model": "small", "language": "zh", "key": "one"}):
        assert not RecognitionProgress(runtime.progress, str(runtime.audio), **kwargs).stage
    with pytest.raises(ValueError):
        checkpoint.save("complete", [{"start": 0, "end": float("nan"), "text": "Hello"}], "en")
    assert RecognitionProgress(runtime.progress, str(runtime.audio), model="small", language="auto", key="one").stage == "aligned"


def test_engine_file_protocol_resumes_with_matching_checkpoint_contract(runtime, tmp_path):
    from linsub.engine import main

    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(json.dumps({"protocol_version": 1, "operation": "transcribe",
        "response_path": str(response_path), "status_path": str(tmp_path / "status.json"), "payload": {
            "audio_path": str(runtime.audio), "output_json_path": str(runtime.output), "video_id": "fixture",
            "source_language": "auto", "model_name": "small", "checkpoint_path": str(runtime.progress),
            "checkpoint_key": "generation-1"}}))
    assert main.run_file_request(request_path) == 0
    assert json.loads(response_path.read_text())["ok"] is True
    runtime.output.unlink()
    assert main.run_file_request(request_path) == 0
    assert json.loads(response_path.read_text())["result"]["detected_language"] == "en"
    assert runtime.decode.call_count == 1


def test_core_forwards_phase_contract_to_engine(runtime, monkeypatch):
    from linsub.pipeline import manual_tools

    worker = Mock(return_value={"segments": [], "detected_language": "en"})
    monkeypatch.setattr("linsub.core.hardware.processing_device_preference", lambda: "gpu")
    monkeypatch.setattr("linsub.core.model_choices.recognition_context", lambda *_: {"model": "small", "device": "gpu"})
    monkeypatch.setattr("linsub.services.external_tasks.run_external_task", worker)
    monkeypatch.setattr(manual_tools.video_store, "log_to_video", Mock())
    manual_tools.transcribe(str(runtime.audio), str(runtime.output), "auto", "fixture",
                            checkpoint_path=str(runtime.progress), checkpoint_key="generation-1")
    assert worker.call_args.args[2]["checkpoint_path"] == str(runtime.progress)
    assert worker.call_args.args[2]["checkpoint_key"] == "generation-1"


def test_completed_phase_does_not_start_or_import_external_asr_engine(runtime, monkeypatch):
    from linsub.pipeline import manual_tools

    expected = runtime.run()
    runtime.output.unlink()
    worker = Mock(side_effect=AssertionError("ASR worker started"))
    monkeypatch.setattr("linsub.core.hardware.processing_device_preference", lambda: "gpu")
    monkeypatch.setattr("linsub.core.model_choices.recognition_context", lambda *_: {"model": "small", "device": "gpu"})
    monkeypatch.setattr("linsub.services.external_tasks.run_external_task", worker)
    monkeypatch.setattr(manual_tools.video_store, "log_to_video", Mock())
    result = manual_tools.transcribe(str(runtime.audio), str(runtime.output), "auto", "fixture",
                                    checkpoint_path=str(runtime.progress), checkpoint_key="generation-1")
    assert result == expected
    assert json.loads(runtime.output.read_text()) == expected[0]
    worker.assert_not_called()
