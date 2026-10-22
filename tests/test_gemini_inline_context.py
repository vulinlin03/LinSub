import json
from unittest.mock import patch

import pytest

from linsub.services import gemini_translation as gemini, translation


def test_neighbor_context_keeps_four_cjk_sentences_with_character_cap():
    texts = ["前" + "甲" * 100 + str(index) for index in range(10)]
    before = gemini._bounded_neighbor_context(texts, 5, 6, before=True)
    after = gemini._bounded_neighbor_context(texts, 5, 6, before=False)

    assert before == texts[1:5]
    assert after == texts[6:10]
    assert len("".join(before)) <= 1200
    assert len("".join(after)) <= 1200


def source_file(tmp_path, count=25, text="Hello friend"):
    source = tmp_path / "source.json"
    source.write_text(json.dumps([
        {"start": i * 2, "end": i * 2 + 1, "text": text, "language": "en"}
        for i in range(count)
    ]), encoding="utf-8")
    return source


@pytest.mark.parametrize("count,text,inline", [
    (32, "Hello friend", True),
    (33, "Hello friend", False),
    (1, "a" * 12000, True),
    (1, "a" * 12001, False),
], ids=["32-cues", "33-cues", "12000-chars", "12001-chars"])
def test_inline_limits_keep_long_story_guide(tmp_path, count, text, inline):
    source = source_file(tmp_path, count, text)
    with (
        patch.object(translation, "log_to_video"),
        patch.object(translation, "shutdown_hymt2_worker"),
        patch.object(translation, "_suspicious_translation_indexes", return_value=[]),
        patch.object(gemini, "create_story_guide", return_value="Full story guide") as guide,
        patch.object(gemini, "translate_texts", return_value=["Xin chào bạn"] * count) as send,
    ):
        translation.translate_segments(
            str(source), str(tmp_path / "out.json"), "fixture",
            provider="gemini", translation_model="gemini-3.5-flash-lite",
        )
    assert guide.call_count == (0 if inline else 1)
    assert send.call_args.kwargs["max_batch_segments"] == 32
    if not inline:
        assert send.call_args.kwargs["story_guide"] == "Full story guide"


def test_ten_short_chinese_cues_are_sent_together_as_inline_context(tmp_path):
    chinese = [
        "师父把药材交给了弟子。", "这是百年灵芝。", "还要继续修炼吗？", "先把丹炉准备好。",
        "药香已经飘出来了。", "这株万年参王十分珍贵。", "炼化之后可以提升修为。",
        "每一甲子都能增加力量。", "不要浪费这次机会。", "明天我们再去山里。",
    ]
    source = tmp_path / "source-zh.json"
    source.write_text(json.dumps([
        {"start": i * 2, "end": i * 2 + 1, "text": text, "language": "zh"}
        for i, text in enumerate(chinese)
    ], ensure_ascii=False), encoding="utf-8")
    seen = {}

    def translate(texts, **kwargs):
        seen["texts"] = list(texts)
        seen.update(kwargs)
        return ["Câu dịch %d" % (index + 1) for index in range(len(texts))]

    with (
        patch.object(translation, "log_to_video"),
        patch.object(translation, "shutdown_hymt2_worker"),
        patch.object(translation, "_suspicious_translation_indexes", return_value=[]),
        patch.object(gemini, "create_story_guide", side_effect=AssertionError("short transcript uses inline context")),
        patch.object(gemini, "translate_texts", side_effect=translate),
    ):
        result = translation.translate_segments(
            str(source), str(tmp_path / "out-zh.json"), "fixture", source_language="auto",
            provider="gemini", translation_model="gemini-3.5-flash-lite",
        )

    assert seen["texts"] == chinese
    assert seen["source_language"] == "Chinese"
    assert seen["max_batch_segments"] == 32
    assert seen["story_guide"] == ""
    assert len(result) == 10


def test_short_resume_retains_full_context_and_saved_names(tmp_path):
    source = source_file(tmp_path, 2)
    output = tmp_path / "out.json"
    output.write_text("previous published result", encoding="utf-8")

    def interrupted(texts, **kwargs):
        kwargs["result_callback"]([0], ["Chào anh Nam"])
        raise RuntimeError("interrupted")

    with (
        patch.object(translation, "log_to_video"),
        patch.object(translation, "shutdown_hymt2_worker"),
        patch.object(gemini, "create_story_guide", side_effect=AssertionError("extra API call")),
        patch.object(gemini, "translate_texts", side_effect=interrupted),
    ):
        with pytest.raises(RuntimeError, match="interrupted"):
            translation.translate_segments(
                str(source), str(output), "fixture", provider="gemini",
                translation_model="gemini-3.5-flash-lite",
            )
    assert output.read_text(encoding="utf-8") == "previous published result"
    requests = []

    def send(chunk, **kwargs):
        requests.append(chunk)
        context = json.loads(kwargs["story_guide"])
        assert len(context["source_transcript"]) == 2
        assert context["completed_translations"] == [{"id": 0, "text": "Chào anh Nam"}]
        return ["Xin chào bạn"]

    with (
        patch.object(translation, "log_to_video"),
        patch.object(translation, "shutdown_hymt2_worker"),
        patch.object(gemini, "log_to_video"),
        patch.object(gemini, "active_key", return_value="fixture"),
        patch.object(gemini, "create_story_guide", side_effect=AssertionError("extra API call")),
        patch.object(gemini, "_request_chunk", side_effect=send),
    ):
        result = translation.translate_segments(
            str(source), str(output), "fixture", provider="gemini",
            translation_model="gemini-3.5-flash-lite",
        )
    assert requests == [[(1, "Hello friend")]]
    assert [row["text"] for row in result] == ["Chào anh Nam", "Xin chào bạn"]


def test_short_validation_repairs_only_bad_cue_with_full_context(tmp_path):
    source = source_file(tmp_path, 2)
    calls = []

    def send(texts, **kwargs):
        calls.append(texts)
        if len(calls) == 1:
            return ["Chào anh Nam", "\ufffd"]
        context = json.loads(kwargs["story_guide"])
        assert len(context["source_transcript"]) == 2
        assert context["completed_translations"] == [{"id": 0, "text": "Chào anh Nam"}]
        return ["Xin chào bạn"]

    with (
        patch.object(translation, "log_to_video"),
        patch.object(translation, "shutdown_hymt2_worker"),
        patch.object(gemini, "create_story_guide", side_effect=AssertionError("extra API call")),
        patch.object(gemini, "translate_texts", side_effect=send),
    ):
        result = translation.translate_segments(
            str(source), str(tmp_path / "out.json"), "fixture", provider="gemini",
            translation_model="gemini-3.5-flash-lite",
        )
    assert len(calls) == 2 and len(calls[1]) == 1
    assert [row["text"] for row in result] == ["Chào anh Nam", "Xin chào bạn"]


def test_semantic_review_retries_invented_uncle_and_collapsed_jiazi(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps([
        {"start": 0, "end": 2, "text": "我們只希望你一生平安", "language": "zh"},
        {"start": 2, "end": 4, "text": "可增十个甲子的修为", "language": "zh"},
    ]), encoding="utf-8")
    calls = []

    def send(texts, **_kwargs):
        calls.append(texts)
        if len(calls) == 1:
            return ["Tụi chú chỉ mong con bình an.", "Có thể tăng thêm sáu mươi năm tu vi."]
        return ["Bọn ta chỉ mong con bình an.", "Có thể tăng thêm 600 năm tu vi."]

    with (
        patch.object(translation, "log_to_video"),
        patch.object(translation, "shutdown_hymt2_worker"),
        patch.object(gemini, "create_story_guide", side_effect=AssertionError("extra API call")),
        patch.object(gemini, "translate_texts", side_effect=send),
    ):
        result = translation.translate_segments(
            str(source), str(tmp_path / "out.json"), "fixture", source_language="zh",
            target_language="vi", provider="gemini", translation_model="gemini-3.5-flash-lite",
        )

    assert len(calls) == 2
    assert calls[1] == ["我們只希望你一生平安", "可增十个甲子的修为"]
    assert [row["text"] for row in result] == [
        "Bọn ta chỉ mong con bình an.", "Có thể tăng thêm 600 năm tu vi.",
    ]
    assert all("translation_warnings" not in row for row in result)


def test_success_timing_log_contains_model_and_usage_without_content():
    import io

    answer = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{
        "text": json.dumps({"translations": [{"id": 0, "text": "Xin chào"}]})
    }]}}], "usageMetadata": {"totalTokenCount": 123}}
    with (
        patch.object(gemini.request, "urlopen", return_value=io.BytesIO(json.dumps(answer).encode())),
        patch.object(gemini.time, "perf_counter", side_effect=[10.0, 12.5]),
        patch.object(gemini, "check_cancellation"),
        patch.object(gemini, "log_to_video") as log,
    ):
        gemini._request_chunk(
            [(0, "private transcript")], key="private API key", model="gemini-3.5-flash-lite",
            source_language="English", target_language="Vietnamese", video_id="fixture",
        )
    message = log.call_args.args[1]
    assert "phase=translation" in message and "elapsed=2.50s" in message
    assert "gemini-3.5-flash-lite" in message and "123" in message
    assert "private" not in message and "Xin chào" not in message
