"""Review must not destroy a received translation or swallow a user's pause."""
import json
from unittest.mock import patch

import pytest

from linsub.services import translation


@pytest.mark.parametrize("text", [
    "Tụi chú chỉ mong con bình an.",
    "Bọn chú chỉ mong con bình an.",
    "Chú chỉ mong con bình an.",
])
def test_review_flags_vietnamese_uncle_inferred_from_chinese_we(text):
    assert translation.is_suspicious_translation(
        "我們只希望你一生平安", text, "Vietnamese",
    )


@pytest.mark.parametrize("text", [
    "Chúng ta chỉ mong con bình an.",
    "Bọn ta chỉ mong con bình an.",
])
def test_review_keeps_neutral_or_established_first_person_plural(text):
    assert not translation.is_suspicious_translation(
        "我們只希望你一生平安", text, "Vietnamese",
    )


def test_review_allows_uncle_when_chinese_source_states_relationship():
    assert not translation.is_suspicious_translation(
        "叔叔只希望你一生平安", "Chú chỉ mong con bình an.", "Vietnamese",
    )


@pytest.mark.parametrize("translation_text", [
    "Có thể tăng thêm sáu mươi năm tu vi.",
    "Có thể tăng thêm 60 năm tu vi.",
])
def test_review_flags_jiazi_multiplier_collapsed_to_one_cycle(translation_text):
    assert translation.is_suspicious_translation(
        "可增十个甲子的修为", translation_text, "Vietnamese",
    )


@pytest.mark.parametrize("translation_text", [
    "Có thể tăng thêm sáu trăm năm tu vi.",
    "Có thể tăng thêm 600 năm tu vi.",
    "Có thể tăng thêm mười giáp tý tu vi.",
])
def test_review_accepts_full_jiazi_multiplier(translation_text):
    assert not translation.is_suspicious_translation(
        "可增十个甲子的修为", translation_text, "Vietnamese",
    )


@pytest.mark.parametrize("mode", ["manual", "auto"])
@pytest.mark.parametrize("failure", [RuntimeError("request timed out"), {}, {1: None}])
def test_failed_recovery_preserves_valid_translation_and_review_warning(tmp_path, mode, failure):
    source, output = tmp_path / "source.json", tmp_path / "translated.json"
    source.write_text(json.dumps([
        {"start": 0, "end": 2, "text": "The farmer gathered grass"},
        {"start": 2, "end": 4, "text": "The cattle waited nearby"}]), encoding="utf-8")
    answer = "Người nông dân gom cỏ"
    with (patch.object(translation, "_translate_with_hymt2_worker", side_effect=[[answer, answer], failure, failure]),
          patch.object(translation, "log_to_video")):
        result = translation.translate_segments(str(source), str(output), "fixture", validation_mode=mode)
    assert [row["text"] for row in result] == [answer, answer]
    assert result[1]["translation_warnings"] == ["review_translation"]
    assert json.loads(output.read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_pause_during_review_does_not_publish(tmp_path, mode):
    source, output = tmp_path / "source.json", tmp_path / "translated.json"
    source.write_text('[{"start":0,"end":1,"text":"Hello friend"}]', encoding="utf-8")
    output.write_text("previous", encoding="utf-8")
    with (patch.object(translation, "_translate_with_hymt2_worker",
                       side_effect=[["Xin\ufffd chào"], RuntimeError("Video cancelled by user.")]),
          patch.object(translation, "log_to_video")):
        with pytest.raises(RuntimeError, match="cancelled"):
            translation.translate_segments(str(source), str(output), "fixture", validation_mode=mode)
    assert output.read_text(encoding="utf-8") == "previous"


def test_corrupt_retry_cannot_replace_valid_initial_translation(tmp_path):
    source, output = tmp_path / "source.json", tmp_path / "translated.json"
    source.write_text(json.dumps([
        {"start": 0, "end": 2, "text": "The farmer gathered grass"},
        {"start": 2, "end": 4, "text": "The cattle waited nearby"}]), encoding="utf-8")
    answer = "Người nông dân gom cỏ"
    with (patch.object(translation, "_translate_with_hymt2_worker",
                       side_effect=[[answer, answer], {1: "\ufffd"}, {1: ""}]),
          patch.object(translation, "log_to_video")):
        result = translation.translate_segments(str(source), str(output), "fixture")
    assert result[1]["text"] == answer
    assert result[1]["translation_warnings"] == ["review_translation"]


@pytest.mark.parametrize("code", ["review_translation", "invalid_text", "review_asr"])
def test_subtitle_review_warning_disappears_after_user_edits_text(code):
    from linsub.desktop.manual_editor_document_model import ManualEditorDocumentModel
    from linsub.schemas.editor import EditorClip, EditorDocument, EditorSequence, EditorTrack

    clip = EditorClip(clip_id="subtitle-1", track_id="subtitles", kind="subtitle",
                      name="Chào bạn", start_ms=0, duration_ms=2000,
                      metadata={"segment_payload": {"translation_warnings": [code],
                                                    "translation_warning_text": "Chào bạn"}})
    document = EditorDocument(video_id="review-fixture", sequence=EditorSequence(duration_ms=2000),
                              tracks=[EditorTrack(track_id="subtitles", kind="subtitle", name="Phụ đề")],
                              clips=[clip])
    model = ManualEditorDocumentModel()
    try:
        model.set_document(document)
        model.selectClip("subtitle-1", False)
        assert [row["code"] for row in model.selectedClip["warnings"]] == [code]
        document.clips[0].name = "Chào mọi người"
        assert model.selectedClip["warnings"] == []
    finally:
        model.close()


def test_asr_review_metadata_reaches_translated_result_and_warning(tmp_path):
    source = tmp_path / "source-review.json"
    source.write_text(json.dumps([{
        "start": 0, "end": 2, "text": "万年参王十分珍贵", "language": "zh",
        "asr_quality": {"status": "review", "reasons": ["low_log_probability"]},
    }], ensure_ascii=False), encoding="utf-8")
    with (
        patch.object(translation, "log_to_video"),
        patch.object(translation, "shutdown_hymt2_worker"),
        patch.object(translation, "_suspicious_translation_indexes", return_value=[]),
        patch("linsub.services.gemini_translation.create_story_guide", side_effect=AssertionError("short transcript uses inline context")),
        patch("linsub.services.gemini_translation.translate_texts", return_value=["Vua sâm vạn năm rất quý"]),
    ):
        result = translation.translate_segments(
            str(source), str(tmp_path / "translated-review.json"), "fixture",
            source_language="zh", target_language="vi", provider="gemini",
            translation_model="gemini-3.5-flash-lite",
        )

    assert result[0]["asr_quality"] == {"status": "review", "reasons": ["low_log_probability"]}
    assert result[0]["translation_warnings"] == ["review_asr"]
