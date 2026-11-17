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


# ---------- TTS ----------
def test_tts_sync_wav(monkeypatch, fast_sleep):
    import json
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_test")
    sent = []

    def fake(method, path, body=None, headers=None, timeout=60):
        sent.append((method, path, json.loads(body), headers))
        return 200, {"_raw": b"RIFF....", "_ctype": "audio/wav"}, {}
    monkeypatch.setattr(voicelab, "_request", fake)
    assert run(voicelab.synthesize("Salom")) == b"RIFF...."
    method, path, payload, headers = sent[0]
    assert (method, path) == ("POST", "/v1/tts")
    assert payload["language"] == "uz" and payload["voice_id"] == config.VOICELAB_TTS_VOICE
    assert payload["voice_id"] == "voice_4ef06e6b-2229-4023-ad66-c2a7a2b736ff"  # Madina — o'zbek ayol ovozi
    assert 8 <= len(headers["Idempotency-Key"]) <= 128


def test_tts_async_polls_and_downloads(monkeypatch, fast_sleep):
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_test")
    polls = [{"status": "processing"}, {"status": "completed", "audio_url": "https://cdn/x.wav"}]

    def fake(method, path, body=None, headers=None, timeout=60):
        if method == "POST":
            return 202, {"id": "gen_1", "status": "queued"}, {}
        assert path == "/v1/tts/generations/gen_1"
        return 200, polls.pop(0), {}
    monkeypatch.setattr(voicelab, "_request", fake)
    monkeypatch.setattr(voicelab, "_download", lambda url, timeout=60: b"WAV:" + url.encode())
    assert run(voicelab.synthesize("Salom")) == b"WAV:https://cdn/x.wav"


def test_tts_voicelab_no_fallback(monkeypatch):
    import tts
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_test")

    async def vl_ok(text, lang):
        return b"OggS-vl"
    monkeypatch.setattr(tts, "_voicelab", vl_ok)
    assert run(tts.synthesize("Assalomu alaykum")) == (b"OggS-vl", "voicelab")

    async def vl_fail(text, lang):
        raise voicelab.VoiceLabError(402, "insufficient_credits")
    monkeypatch.setattr(tts, "_voicelab", vl_fail)
    with pytest.raises(voicelab.VoiceLabError) as ei:
        run(tts.synthesize("Assalomu alaykum"))
    assert "kredit tugagan" in voicelab.explain_error(ei.value)
