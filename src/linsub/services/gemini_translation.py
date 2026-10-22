"""Bounded, per-project subtitle translation through the Gemini REST API."""

from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib import error, request

from linsub.pipeline.process_registry import check_cancellation, is_paused
from linsub.config import RUNTIME_DATA_DIR
from linsub.services import secure_credentials
from linsub.services.video_store import log_to_video
from linsub.services.translation_timing import timing_budget

CREDENTIAL_TARGET = "LinSub/Gemini/APIKey"
API_KEYS_URL = "https://aistudio.google.com/api-keys"
MODELS = {
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
    "gemini-3.8-flash": "Gemini 3.8 Flash",
}
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_TRANSLATION_NEIGHBOR_CONTEXT_SENTENCES = 4
_TRANSLATION_NEIGHBOR_CONTEXT_CHARACTERS = 1200


def _bounded_neighbor_context(texts: list[str], start: int, end: int, *, before: bool) -> list[str]:
    """Select nearby subtitle lines by character count, preserving CJK text intact."""
    if before:
        indexes = list(range(max(0, start - _TRANSLATION_NEIGHBOR_CONTEXT_SENTENCES), start))
        indexes.reverse()
    else:
        indexes = range(end, min(len(texts), end + _TRANSLATION_NEIGHBOR_CONTEXT_SENTENCES))
    selected = []
    used_characters = 0
    for index in indexes:
        text = str(texts[index] or "")
        if not text or used_characters + len(text) > _TRANSLATION_NEIGHBOR_CONTEXT_CHARACTERS:
            continue
        selected.append(text)
        used_characters += len(text)
    return list(reversed(selected)) if before else selected
_KEYS_PATH = Path(RUNTIME_DATA_DIR) / "gemini-api-keys.json"
_KEYS_LOCK = threading.RLock()


def _key_index() -> dict:
    try:
        data = json.loads(_KEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    entries = data.get("keys") if isinstance(data, dict) else []
    keys = [
        {"id": entry["id"], "label": entry["label"]}
        for entry in entries if isinstance(entry, dict)
        and isinstance(entry.get("id"), str) and isinstance(entry.get("label"), str)
        and entry["id"] and entry["label"]
    ] if isinstance(entries, list) else []
    return {"active": str(data.get("active") or "legacy") if isinstance(data, dict) else "legacy", "keys": keys}


def _save_key_index(index: dict) -> None:
    _KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".gemini-keys-", suffix=".tmp", dir=_KEYS_PATH.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(index, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _KEYS_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _credential_target(key_id: str) -> str:
    return CREDENTIAL_TARGET if key_id == "legacy" else f"{CREDENTIAL_TARGET}/{key_id}"


def list_keys() -> list[dict[str, str | bool]]:
    """Return labels and active state only; secrets never cross into QML."""
    with _KEYS_LOCK:
        index = _key_index()
        entries = [{"id": "legacy", "label": "Key mặc định"}] if secure_credentials.read_secret(CREDENTIAL_TARGET).strip() else []
        entries += index["keys"]
        return [{**entry, "active": entry["id"] == index["active"]} for entry in entries]


def active_key() -> str:
    with _KEYS_LOCK:
        index = _key_index()
        return secure_credentials.read_secret(_credential_target(index["active"])).strip()


def add_named_key(label: str, value: str) -> str:
    name = str(label or "").strip()
    if not name or len(name) > 64:
        raise ValueError("Đặt tên key từ 1 đến 64 ký tự.")
    key = str(value or "").strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("API key không hợp lệ. Hãy sao chép lại từ Google AI Studio.")
    with _KEYS_LOCK:
        index = _key_index()
        key_id = uuid.uuid4().hex
        target = _credential_target(key_id)
        secure_credentials.write_secret(target, key, username="Gemini API")
        index["keys"].append({"id": key_id, "label": name})
        index["active"] = key_id
        try:
            _save_key_index(index)
        except OSError:
            secure_credentials.delete_secret(target)
            raise
        return key_id


def select_key(key_id: str) -> None:
    with _KEYS_LOCK:
        index = _key_index()
        known = {entry["id"] for entry in index["keys"]}
        if key_id not in known and key_id != "legacy":
            raise ValueError("Không tìm thấy API key đã chọn.")
        if not secure_credentials.read_secret(_credential_target(key_id)).strip():
            raise ValueError("API key này không còn trong Windows Credential Manager.")
        index["active"] = key_id
        _save_key_index(index)


def remove_key(key_id: str) -> bool:
    with _KEYS_LOCK:
        index = _key_index()
        if key_id != "legacy" and not any(entry["id"] == key_id for entry in index["keys"]):
            return False
        index["keys"] = [entry for entry in index["keys"] if entry["id"] != key_id]
        if index["active"] == key_id:
            index["active"] = index["keys"][0]["id"] if index["keys"] else "legacy"
        target = _credential_target(key_id)
        previous_secret = secure_credentials.read_secret(target)
        deleted = secure_credentials.delete_secret(target)
        try:
            _save_key_index(index)
        except OSError:
            if deleted and previous_secret:
                secure_credentials.write_secret(target, previous_secret, username="Gemini API")
            raise
        return deleted


def key_configured() -> bool:
    return bool(active_key())


def save_key(value: str) -> None:
    key = str(value or "").strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("API key không hợp lệ. Hãy sao chép lại từ Google AI Studio.")
    with _KEYS_LOCK:
        secure_credentials.write_secret(CREDENTIAL_TARGET, key, username="Gemini API")
        index = _key_index()
        index["active"] = "legacy"
        _save_key_index(index)


def clear_key() -> bool:
    return remove_key("legacy")


def _chunks(texts: list[str], *, max_segments: int = 24):
    chunk: list[tuple[int, str]] = []
    chars = 0
    for index, text in enumerate(texts):
        # Tăng giới hạn ký tự mỗi chunk để ít yêu cầu Gemini hơn
        if chunk and (len(chunk) >= max_segments or chars + len(text) > 8000):
            yield chunk
            chunk, chars = [], 0
        chunk.append((index, text))
        chars += len(text)
    if chunk:
        yield chunk


def _story_text_chunks(texts: list[str], limit: int = 12000):
    """Group ordered transcript lines into bounded story-analysis requests."""
    group: list[tuple[int, str]] = []
    size = 0
    for index, text in enumerate(texts):
        line_size = len(text) + 32
        if group and size + line_size > limit:
            yield group
            group, size = [], 0
        group.append((index, text))
        size += line_size
    if group:
        yield group


def _request_story_note(
    entries: list[tuple[int, str]], *, key: str, model: str,
    source_language: str, target_language: str, video_id: str = "",
    merge_notes: bool = False,
) -> str:
    ordered = [{"id": index, "text": text} for index, text in entries]
    task = (
        "Merge the chronological notes into one concise guide for translating the entire story. "
        "Keep stable character names, relationships, forms of address, important terms, plot facts, "
        "and tone. Resolve duplicate spellings consistently. Do not add facts not supported by the notes."
        if merge_notes else
        "Read these chronological transcript lines as part of one continuous story. Write a compact guide "
        "in " + target_language + " with the plot events, character names and relationships, stable name "
        "renderings, recurring terms, and tone. Mark uncertain or speech-recognition-corrupted details as "
        "uncertain instead of guessing. Preserve chronology and do not translate the dialogue line by line."
    )
    language_hint = (
        "These are already summarized notes in the target language."
        if merge_notes else "Source language: " + source_language + "."
    )
    payload = {
        "systemInstruction": {"parts": [{"text": (
            task + " " + language_hint + " Keep the guide concise and useful for "
            "consistent subtitle and dubbing translation."
        )}]},
        "contents": [{"role": "user", "parts": [{
            "text": json.dumps(ordered, ensure_ascii=False, separators=(",", ":")),
        }]}],
        "generationConfig": {
            "temperature": 0.1,
            "candidateCount": 1,
            "maxOutputTokens": 1200,
            "thinkingConfig": {"thinkingLevel": "minimal" if model in {"gemini-3.1-flash-lite", "gemini-3.5-flash-lite"} else "low", "includeThoughts": False},
        },
    }
    url = _ENDPOINT.format(model=model)
    for attempt in range(3):
        check_cancellation(video_id)
        answer = None
        started = time.perf_counter()
        try:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            outgoing = request.Request(
                url, data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
                method="POST",
            )
            with request.urlopen(outgoing, timeout=90) as response:
                answer = json.load(response)
            candidates = answer.get("candidates") if isinstance(answer, dict) else None
            content = candidates[0].get("content") if candidates and isinstance(candidates[0], dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            note = "\n".join(
                part["text"] for part in parts or []
                if isinstance(part, dict) and not part.get("thought") and isinstance(part.get("text"), str)
            ).strip()
            if not note:
                raise ValueError("Gemini returned an empty story guide.")
            if video_id:
                log_to_video(
                    video_id,
                    f"Gemini {model}: phase={'guide_merge' if merge_notes else 'guide'}, "
                    f"elapsed={time.perf_counter() - started:.2f}s, attempt={attempt + 1}/3, "
                    f"sentences={len(entries)}, {_response_diagnostic(answer)}.",
                    component="TRANSLATE",
                )
            check_cancellation(video_id)
            return note[:5000]
        except error.HTTPError as exc:
            if exc.code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                _retry_delay(min(8, 2 ** attempt + random.random()), video_id)
                continue
            raise RuntimeError(f"Gemini không tạo được hướng dẫn ngữ cảnh (HTTP {exc.code}).") from None
        except (error.URLError, TimeoutError):
            if attempt < 2:
                _retry_delay(min(8, 2 ** attempt + random.random()), video_id)
                continue
            raise RuntimeError("Không kết nối được Gemini khi phân tích mạch truyện.") from None
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            if attempt < 2:
                _retry_delay(min(8, 2 ** attempt + random.random()), video_id)
                continue
            raise RuntimeError("Gemini không trả về hướng dẫn ngữ cảnh hợp lệ.") from None
    raise RuntimeError("Gemini không phản hồi khi phân tích mạch truyện.")


def create_story_guide(
    texts: list[str], *, model: str, source_language: str, target_language: str,
    video_id: str = "", cache_path: str | Path | None = None, status_callback=None,
) -> str:
    """Create and locally cache a compact, whole-story translation guide."""
    signature = hashlib.sha256(json.dumps({
        "texts": texts, "model": model, "source_language": source_language,
        "target_language": target_language,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    cache = Path(cache_path) if cache_path else None
    cached_notes: list[str] = []
    if cache is not None:
        try:
            stored = json.loads(cache.read_text(encoding="utf-8"))
            if (stored.get("signature") == signature and isinstance(stored.get("guide"), str)
                    and stored["guide"].strip()):
                return stored["guide"]
            if (stored.get("signature") == signature and isinstance(stored.get("notes"), list)
                    and all(isinstance(note, str) for note in stored["notes"])):
                cached_notes = stored["notes"]
        except (OSError, ValueError, AttributeError, UnicodeDecodeError):
            pass

    key = active_key()
    if not key:
        raise RuntimeError("Chưa có Gemini API key. Vào Cài đặt → Quản lý API Key để thêm key.")
    groups = list(_story_text_chunks(texts))
    notes = list(cached_notes[:len(groups)])
    for index, group in enumerate(groups[len(notes):], len(notes) + 1):
        detail = f"Analyzing story context: part {index}/{len(groups)}."
        log_to_video(video_id, detail, component="TRANSLATE")
        if status_callback:
            status_callback(detail)
        notes.append(_request_story_note(
            group, key=key, model=model, source_language=source_language,
            target_language=target_language, video_id=video_id,
        ))
        if cache is not None:
            _save_story_guide_cache(cache, signature, notes)
    while len(notes) > 1:
        merged: list[str] = []
        for group in _story_text_chunks(notes):
            merged.append(_request_story_note(
                group, key=key, model=model, source_language=source_language,
                target_language=target_language, video_id=video_id, merge_notes=True,
            ))
        notes = merged
    guide = notes[0] if notes else "No story context available."
    if cache is not None:
        _save_story_guide_cache(cache, signature, notes, guide=guide)
    return guide


def _save_story_guide_cache(cache: Path, signature: str, notes: list[str], *, guide: str = "") -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".story-guide-", suffix=".tmp", dir=cache.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"signature": signature, "notes": notes, "guide": guide}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, cache)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class _ResponseError(ValueError):
    def __init__(self, reason: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


def _parse_translations(answer, chunk: list[tuple[int, str]]) -> list[str]:
    """Accept complete answers only; never repair/truncate subtitle content."""
    invalid = "Gemini không trả về dữ liệu dịch hợp lệ. Dữ liệu cũ được giữ nguyên."
    if not isinstance(answer, dict):
        raise _ResponseError("invalid_envelope", invalid)
    feedback = answer.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason") not in {None, "", "BLOCK_REASON_UNSPECIFIED"}:
        raise _ResponseError("blocked_prompt", "Gemini chặn nội dung yêu cầu. Dữ liệu cũ được giữ nguyên.", retryable=False)
    candidates = answer.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise _ResponseError("missing_candidate", invalid)
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish == "MAX_TOKENS":
        raise _ResponseError("token_limit", "Gemini dừng trước khi dịch đủ câu. Dữ liệu cũ được giữ nguyên.")
    if finish not in {None, "", "STOP", "FINISH_REASON_UNSPECIFIED", "OTHER", "MALFORMED_RESPONSE"}:
        raise _ResponseError("blocked_response", "Gemini không hoàn tất bản dịch do giới hạn nội dung. Dữ liệu cũ được giữ nguyên.", retryable=False)
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise _ResponseError("missing_parts", invalid)
    text = "".join(part["text"] for part in parts if isinstance(part, dict)
                   and not part.get("thought") and isinstance(part.get("text"), str)).strip()
    # Tolerate a complete code fence, not arbitrary prose or partial JSON.
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"} and lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        raise _ResponseError("invalid_json", invalid) from None
    translations = parsed.get("translations") if isinstance(parsed, dict) else None
    if not isinstance(translations, list) or len(translations) != len(chunk):
        raise _ResponseError("missing_sentences", "Gemini trả về thiếu câu dịch. Dữ liệu cũ được giữ nguyên.")
    expected = {index for index, _ in chunk}
    output = {}
    for item in translations:
        if (not isinstance(item, dict) or type(item.get("id")) is not int
                or item["id"] not in expected or item["id"] in output
                or not isinstance(item.get("text"), str) or not item["text"].strip()):
            raise _ResponseError("invalid_sentence_mapping", "Gemini trả về sai mã câu hoặc câu dịch rỗng. Dữ liệu cũ được giữ nguyên.")
        output[item["id"]] = item["text"].strip()
    return [output[index] for index, _ in chunk]


def _response_diagnostic(answer) -> str:
    """Only allowlisted counters/state; no raw response, subtitle or secret."""
    if not isinstance(answer, dict):
        return "finish=unknown"
    candidates = answer.get("candidates")
    candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
    finish = candidate.get("finishReason")
    safe_finish = finish if isinstance(finish, str) and finish in {
        "STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT",
        "SPII", "LANGUAGE", "OTHER", "MALFORMED_RESPONSE", "FINISH_REASON_UNSPECIFIED",
    } else "unknown"
    values = [f"finish={safe_finish}"]
    usage = answer.get("usageMetadata")
    if isinstance(usage, dict):
        for field in ("candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount"):
            value = usage.get(field)
            if type(value) is int and value >= 0:
                values.append(f"{field}={value}")
    return ", ".join(values)


def _retry_delay(seconds: float, video_id: str) -> None:
    # Pause/cancel must also interrupt backoff, not only the next HTTP request.
    remaining = seconds
    while remaining > 0:
        check_cancellation(video_id)
        interval = min(.1, remaining)
        time.sleep(interval)
        remaining -= interval


def _request_chunk(
    chunk: list[tuple[int, str]], *, key: str, model: str,
    source_language: str, target_language: str, video_id: str = "",
    speech_durations: list[float] | None = None,
    story_guide: str = "",
    source_quality: list[dict | None] | None = None,
    context_before: list[str] | None = None,
    context_after: list[str] | None = None,
) -> list[str]:
    entries = [{"id": index, "text": text} for index, text in chunk]
    for entry in entries:
        index = entry["id"]
        if speech_durations is not None:
            entry.update(timing_budget(speech_durations[index], target_language))
        quality = source_quality[index] if source_quality is not None else None
        if isinstance(quality, dict) and quality.get("status") == "review":
            entry["asr_quality"] = {
                "status": "review",
                "reasons": [str(reason) for reason in quality.get("reasons", []) if str(reason)],
            }
    timing = (
        " Each entry's speech_seconds is its original speaking duration. Use brief, natural spoken phrasing "
        "that fits a similar duration; target_spoken_units/spoken_unit are soft length guidance, not output fields. "
        "Never pad short originals. Prefer concise equivalents without losing facts, negation, names, numbers or tone. "
        "Accuracy takes priority over the approximate length target."
        if speech_durations is not None else ""
    )
    payload = {
        "systemInstruction": {"parts": [{"text": (
            "Translate each subtitle into " + target_language + ". Source language: "
            + source_language + ". Keep the same id and order. Preserve meaning, "
            "speaker tone, names and punctuation. Source text may contain speech-recognition errors; "
            "when the whole-story guide strongly supports an obvious correction, use that interpretation; "
            "otherwise stay close to the supplied words and do not guess. For Chinese, do not "
            "translate personal names character-by-character as ordinary words; render names by pronunciation. "
            "Read all entries in this chronological batch together as context. Infer stable names, "
            "relationships, forms of address and recurring terms from the entire batch and keep them consistent. "
            + ("Use the whole-story guide below to keep plot facts and terminology consistent across the video. "
               if story_guide else "Use only facts supported by the complete batch; do not invent missing story details. ")
            + "The request includes up to four neighboring source sentences before and after this batch, "
            "limited to 1,200 characters per side; use them "
            "only to resolve references, names, technical terms, numbers and poetic phrases. Entries marked "
            "asr_quality=review have weak Whisper decoding diagnostics: use nearby context to correct only an "
            "obvious recognition error, preserve uncertain source meaning, and never invent missing words. "
             "For Chinese-to-Vietnamese, preserve the speaker's grammatical person and established form of address. "
             "Words such as 我们/咱们 mean 'we'; do not infer an uncle/aunt or other family role from them. "
             "Use 'chúng ta' or a clearly established matching register such as 'bọn ta'; use 'chú/bọn chú' only "
             "when the Chinese source explicitly establishes that kinship role. Preserve every numeral and its "
             "multiplier when converting units: one 甲子 is 60 years, so 十个甲子 is 600 years, not 60. "
             "For literary or poetic Chinese, translate the supported meaning naturally rather than rendering "
             "obvious ASR homophones as literal nonsense; if the intended phrase remains uncertain, stay conservative. "
            + "Translate only each entry's text; never translate context or guide text as a subtitle. "
            "Return concise, natural spoken translations; "
            "never merge, omit or invent a subtitle." + timing
            + (" Whole-story guide: " + story_guide if story_guide else "")
        )}]},
        "contents": [{"role": "user", "parts": [{
            "text": json.dumps({
                "context_before": list(context_before or []),
                "entries": entries,
                "context_after": list(context_after or []),
            }, ensure_ascii=False, separators=(",", ":")),
        }]}],
        "generationConfig": {
            "temperature": 0.1,
            "candidateCount": 1,
            # A cap does not prepay tokens. Leave room for language expansion,
            # JSON framing and low-level reasoning so the last cue is not cut.
            "maxOutputTokens": min(8192, max(2048, 1024 + len(chunk) * 64, sum(len(text) for _, text in chunk) * 4)),
            "thinkingConfig": {"thinkingLevel": "minimal" if model in {"gemini-3.1-flash-lite", "gemini-3.5-flash-lite"} else "low", "includeThoughts": False},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {"translations": {"type": "ARRAY", "items": {
                    "type": "OBJECT", "properties": {
                        "id": {"type": "INTEGER"}, "text": {"type": "STRING"}},
                    "required": ["id", "text"]}}},
                "required": ["translations"],
            },
        },
    }
    url = _ENDPOINT.format(model=model)
    for attempt in range(3):
        check_cancellation(video_id)
        answer = None
        started = time.perf_counter()
        try:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            outgoing = request.Request(
                url, data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
                method="POST",
            )
            with request.urlopen(outgoing, timeout=90) as response:
                answer = json.load(response)
            # A valid response already paid for must reach the durable batch
            # callback before honoring Pause. Never publish it as final here.
            values = _parse_translations(answer, chunk)
            if video_id:
                log_to_video(
                    video_id,
                    f"Gemini {model}: phase=translation, elapsed={time.perf_counter() - started:.2f}s, "
                    f"attempt={attempt + 1}/3, sentences={len(chunk)}, {_response_diagnostic(answer)}.",
                    component="TRANSLATE",
                )
            return values
        except error.HTTPError as exc:
            if video_id:
                log_to_video(
                    video_id, f"Gemini {model}: HTTP {exc.code}, attempt {attempt + 1}/3, "
                    f"elapsed={time.perf_counter() - started:.2f}s.",
                    component="TRANSLATE", level="WARNING",
                )
            if exc.code in {408, 429, 500, 502, 503, 504} and attempt < 2:
                _retry_delay(min(8, 2 ** attempt + random.random()), video_id)
                continue
            if exc.code in {401, 403}:
                raise RuntimeError(
                    "Gemini từ chối quyền truy cập (HTTP " + str(exc.code) + "). "
                    "Kiểm tra API key, quyền dùng model và thanh toán trong Google AI Studio."
                ) from None
            if exc.code == 402:
                raise RuntimeError(
                    "Gemini yêu cầu thanh toán (HTTP 402). Kiểm tra số dư và Billing trong Google AI Studio."
                ) from None
            if exc.code == 429:
                raise RuntimeError(
                    "Gemini đã hết quota hoặc đang giới hạn tốc độ. Thử lại sau trong Google AI Studio."
                ) from None
            if exc.code in {500, 502, 503, 504}:
                raise RuntimeError(
                    f"{MODELS[model]}: dịch vụ Google tạm thời không khả dụng (HTTP {exc.code}). "
                    "Thử lại sau hoặc chọn Flash-Lite."
                ) from None
            raise RuntimeError(f"Gemini không xử lý được yêu cầu (HTTP {exc.code}).") from None
        except (error.URLError, TimeoutError):
            if video_id:
                log_to_video(
                    video_id, f"Gemini {model}: connection failure, attempt={attempt + 1}/3, "
                    f"elapsed={time.perf_counter() - started:.2f}s.",
                    component="TRANSLATE", level="WARNING",
                )
            if attempt < 2:
                _retry_delay(min(8, 2 ** attempt + random.random()), video_id)
                continue
            raise RuntimeError("Không kết nối được Gemini. Kiểm tra mạng rồi thử lại.") from None
        except (TypeError, ValueError) as exc:
            check_cancellation(video_id)
            failure = exc if isinstance(exc, _ResponseError) else _ResponseError(
                "invalid_envelope_json", "Gemini không trả về dữ liệu dịch hợp lệ. Dữ liệu cũ được giữ nguyên.",
            )
            retry = failure.retryable and attempt < 2
            if video_id:
                log_to_video(video_id, f"Gemini {model}: response={failure.reason}, {_response_diagnostic(answer)}, "
                             f"attempt={attempt + 1}/3, sentences={len(chunk)}, "
                             f"elapsed={time.perf_counter() - started:.2f}s, "
                             f"token_limit={payload['generationConfig']['maxOutputTokens']}, retry={retry}.",
                             component="TRANSLATE", level="WARNING")
            if not retry:
                raise RuntimeError(str(failure)) from None
            if failure.reason == "token_limit":
                payload["generationConfig"]["maxOutputTokens"] = 8192
    raise RuntimeError("Gemini không phản hồi sau các lần thử.")


def translate_texts(
    texts: list[str], *, model: str, source_language: str,
    target_language: str, video_id: str, progress_callback=None,
    initial_translations: list[str | None] | None = None, result_callback=None,
    speech_durations: list[float] | None = None,
    story_guide: str = "",
    max_batch_segments: int = 24,
    source_quality: list[dict | None] | None = None,
) -> list[str]:
    if source_quality is not None and len(source_quality) != len(texts):
        raise ValueError("Each source sentence must have one ASR quality slot.")
    if speech_durations is not None:
        if len(speech_durations) != len(texts):
            raise ValueError("Each source sentence must have a speech duration.")
        for duration in speech_durations:
            timing_budget(duration, target_language)
    if model not in MODELS:
        raise ValueError("Model Gemini không được hỗ trợ.")
    key = active_key()
    if not key:
        raise RuntimeError("Chưa có Gemini API key. Vào Cài đặt → Quản lý API Key để thêm key.")
    output = list(initial_translations) if initial_translations is not None else [None] * len(texts)
    if len(output) != len(texts):
        raise ValueError("Each source sentence must have a translation slot.")
    if type(max_batch_segments) is not int or max_batch_segments < 1:
        raise ValueError("Gemini translation batch size must be a positive integer.")

    # Gom các chunk cần dịch trước, bỏ những câu đã có bản dịch.
    pending_chunks: list[list[tuple[int, str]]] = []
    for chunk in _chunks(texts, max_segments=max_batch_segments):
        chunk = [(index, text) for index, text in chunk if output[index] is None]
        if chunk:
            pending_chunks.append(chunk)
    if not pending_chunks:
        return output
    for chunk in pending_chunks:
        detail = (
            f"Sending Gemini translation batch: sentences {chunk[0][0] + 1}-"
            f"{chunk[-1][0] + 1} of {len(texts)}."
        )
        log_to_video(video_id, detail, component="TRANSLATE", level="INFO")
        if progress_callback:
            progress_callback(
                sum(value is not None for value in output), len(texts), detail,
            )

    # Các chunk độc lập với nhau nên gửi song song: mỗi chunk là một yêu cầu
    # Gemini độc lập, chạy đồng thời để không phải chờ nối tiếp.
    # Giới hạn số luồng để không vượt quota tốc độ của Gemini.
    max_workers = min(8, len(pending_chunks))
    chunk_results: dict[int, list[str]] = {}
    chunk_lock = threading.Lock()

    def _translate_one(chunk: list[tuple[int, str]]) -> None:
        """Gửi 1 chunk tới Gemini và lưu kết quả."""
        check_cancellation(video_id)
        values = _request_chunk(
            chunk, key=key, model=model,
            source_language=source_language, target_language=target_language, video_id=video_id,
            speech_durations=speech_durations,
            story_guide=story_guide,
            source_quality=source_quality,
            context_before=_bounded_neighbor_context(
                texts, chunk[0][0], chunk[-1][0] + 1, before=True,
            ),
            context_after=_bounded_neighbor_context(
                texts, chunk[0][0], chunk[-1][0] + 1, before=False,
            ),
        )
        with chunk_lock:
            chunk_results[chunk[0][0]] = values

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_translate_one, chunk): chunk for chunk in pending_chunks}
        for future in as_completed(futures):
            chunk = futures[future]
            # Ném lỗi của chunk này ra ngoài để dừng cả lượt dịch.
            future.result()
            values = chunk_results.get(chunk[0][0], [])
            # Pause preserves a just-received batch; Cancel must not write new
            # progress into a workspace that is being discarded/deleted.
            if not is_paused(video_id):
                check_cancellation(video_id)
            for (index, _), value in zip(chunk, values):
                output[index] = value
            if result_callback:
                result_callback([index for index, _ in chunk], values)
            check_cancellation(video_id)
            log_to_video(
                video_id, f"Gemini translated {chunk[-1][0] + 1} of {len(texts)} sentences.",
                component="TRANSLATE", level="INFO",
            )
            if progress_callback:
                progress_callback(
                    sum(value is not None for value in output), len(texts), "Đang dịch bằng Gemini"
                )
    return output
