import pytest

import ai
import config
import voicelab
from conftest import run


@pytest.fixture
def vl(monkeypatch, fast_sleep):
    """VoiceLab API o'rniga soxta javoblar: calls — yuborilgan so'rovlar."""
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_test")
    state = {"calls": [], "polls": [{"status": "processing"}, {"status": "completed", "transcript": " Salom "}],
             "submit_errors": []}

    def fake(method, path, body=None, headers=None, timeout=60):
        state["calls"].append((method, path, dict(headers or {}), body))
        if method == "POST":
            if state["submit_errors"]:
                raise state["submit_errors"].pop(0)
            return 202, {"id": "stt_1", "status": "queued"}, {}
        if method == "GET":
            return 200, state["polls"].pop(0), {}
        return 204, {}, {}

    monkeypatch.setattr(voicelab, "_request", fake)
    return state


def test_transcribe_submits_polls_and_deletes(vl):
    assert run(voicelab.transcribe(b"OggS...", "voice.ogg")) == "Salom"
    methods = [(m, p) for m, p, _, _ in vl["calls"]]
    assert methods == [("POST", "/v1/stt"), ("GET", "/v1/stt/transcriptions/stt_1"),
                       ("GET", "/v1/stt/transcriptions/stt_1"), ("DELETE", "/v1/stt/transcriptions/stt_1")]
    _, _, headers, body = vl["calls"][0]
    assert len(headers["Idempotency-Key"]) == 36 and headers["Content-Type"].startswith("multipart/form-data")
    assert b'name="language"\r\n\r\nauto' in body and b'filename="voice.ogg"' in body
    assert b"Content-Type: audio/ogg" in body and b"OggS..." in body


def test_retry_keeps_idempotency_key(vl):
    vl["submit_errors"] = [voicelab.VoiceLabError(503, "stt_unavailable")]
    assert run(voicelab.transcribe(b"x", "a.ogg")) == "Salom"
    keys = [h["Idempotency-Key"] for m, _, h, _ in vl["calls"] if m == "POST"]
    assert len(keys) == 2 and keys[0] == keys[1]  # qayta yuborishda ikki marta pul yechilmaydi


def test_no_speech_and_failure(vl):
    vl["submit_errors"] = [voicelab.VoiceLabError(422, "no_speech_detected")]
    assert run(voicelab.transcribe(b"x")) == ""
    vl["polls"] = [{"status": "failed", "error": {"code": "invalid_audio"}}]
    with pytest.raises(voicelab.VoiceLabError):
        run(voicelab.transcribe(b"x"))


def test_ai_transcribe_falls_back_to_openai(vl, monkeypatch):
    vl["submit_errors"] = [voicelab.VoiceLabError(401, "invalid_api_key")]

    async def openai_stt(data, filename):
        return "openai matni"
    monkeypatch.setattr(ai, "_openai_transcribe", openai_stt)
    assert run(ai.transcribe(b"x", "voice.ogg")) == "openai matni"
    # VoiceLab ishlasa — OpenAI chaqirilmaydi
    assert run(ai.transcribe(b"x", "voice.ogg")) == "Salom"


def test_ai_transcribe_uses_openai_without_key(monkeypatch):
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "")

    async def openai_stt(data, filename):
        return "openai"
    monkeypatch.setattr(ai, "_openai_transcribe", openai_stt)
    assert run(ai.transcribe(b"x")) == "openai"
