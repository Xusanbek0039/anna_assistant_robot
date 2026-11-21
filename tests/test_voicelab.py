import pytest

import ai
import config
import voicelab
from conftest import run


@pytest.fixture(autouse=True)
def _fresh_key_state(monkeypatch):
    """Har test toza holatdan: joriy kalit yo'q, tugaganlar yo'q, bazadan o'qilmaydi."""
    monkeypatch.setattr(voicelab, "_state", {"current": None, "loaded": True})
    monkeypatch.setattr(voicelab, "_exhausted", {})


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


# ---------- bir nechta kalit ----------
def _key_server(monkeypatch, empty: set):
    """Soxta VoiceLab: `empty` dagi kalitlarda kredit yo'q. (used, alerts) qaytaradi."""
    used, alerts = [], []

    def fake(method, path, body=None, headers=None, timeout=60):
        key = voicelab._current_key.get()
        used.append(key)
        if key in empty:
            raise voicelab.VoiceLabError(402, "insufficient_credits")
        return 200, {"_raw": b"RIFF-" + key.encode()}, {}

    async def notify(text):
        alerts.append(text)
    monkeypatch.setattr(voicelab, "_request", fake)
    monkeypatch.setattr(voicelab, "_notify_owner", notify)
    return used, alerts


def test_switches_to_next_key_when_credits_run_out(monkeypatch, fast_sleep):
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_aaaa1111, vlk_bbbb2222,vlk_cccc3333")
    empty = {"vlk_aaaa1111"}
    used, alerts = _key_server(monkeypatch, empty)

    assert run(voicelab.synthesize("Salom")) == b"RIFF-vlk_bbbb2222"
    assert used == ["vlk_aaaa1111", "vlk_bbbb2222"]
    assert len(alerts) == 1 and "1-kalit" in alerts[0] and "2-kalit (…2222) ga o'tildi" in alerts[0]

    used.clear()  # keyingi so'rov — darhol 2-kalit, xabar takrorlanmaydi
    assert run(voicelab.synthesize("Salom")) == b"RIFF-vlk_bbbb2222"
    assert used == ["vlk_bbbb2222"] and len(alerts) == 1

    # 1-kalitning kredit tugash belgisi eskirsa ham — 2-kalit tugamaguncha o'sha ishlatiladi
    voicelab._exhausted["vlk_aaaa1111"] = 0
    used.clear()
    run(voicelab.synthesize("Salom"))
    assert used == ["vlk_bbbb2222"]


def test_keys_rotate_back_to_first_after_last(monkeypatch, fast_sleep):
    """1 -> 2 -> 3 -> yana 1 (1-kalitga kredit to'ldirilgan)."""
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_k1,vlk_k2,vlk_k3")
    empty = {"vlk_k1"}
    used, alerts = _key_server(monkeypatch, empty)
    assert run(voicelab.synthesize("a")) == b"RIFF-vlk_k2"
    empty.add("vlk_k2")
    assert run(voicelab.synthesize("a")) == b"RIFF-vlk_k3"
    assert "2-kalit" in alerts[-1] and "3-kalit (…k_k3) ga o'tildi" in alerts[-1]

    empty.clear()
    empty.update({"vlk_k2", "vlk_k3"})  # 3-kalit ham tugadi, 1-kalitga esa kredit qo'shildi
    used.clear()
    assert run(voicelab.synthesize("a")) == b"RIFF-vlk_k1"
    assert used == ["vlk_k3", "vlk_k1"]  # 2-kalit yaqinda tugagan — o'tkazib yuborildi
    assert "3-kalit" in alerts[-1] and "1-kalit (…k_k1) ga o'tildi" in alerts[-1]
    assert "kredit yangilangan" in alerts[-1]
    used.clear()
    run(voicelab.synthesize("a"))
    assert used == ["vlk_k1"]  # endi 1-kalit joriy


def test_many_keys_supported(monkeypatch, fast_sleep):
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_1\nvlk_2; vlk_3 vlk_4,vlk_5,vlk_6,vlk_6")
    assert voicelab.keys() == ["vlk_1", "vlk_2", "vlk_3", "vlk_4", "vlk_5", "vlk_6"]
    empty = {"vlk_1", "vlk_2", "vlk_3", "vlk_4", "vlk_5"}
    used, alerts = _key_server(monkeypatch, empty)
    assert run(voicelab.synthesize("a")) == b"RIFF-vlk_6"
    assert len(alerts) == 1 and "6-kalit (…lk_6) ga o'tildi" in alerts[0]
    empty.add("vlk_6")
    empty.discard("vlk_1")
    used.clear()
    assert run(voicelab.synthesize("a")) == b"RIFF-vlk_1"
    assert used == ["vlk_6", "vlk_1"]


def test_key_state_survives_restart(monkeypatch, fast_sleep, fake_storage):
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_k1,vlk_k2,vlk_k3")
    used, alerts = _key_server(monkeypatch, {"vlk_k1"})
    run(voicelab.synthesize("a"))
    saved = fake_storage.d_runtime["voicelab"]
    assert "vlk_k2" not in str(saved) and saved["current"] == voicelab._fp("vlk_k2")  # kalitning o'zi yozilmaydi

    # qayta ishga tushish: xotira toza, bazadan o'qiladi — 1-kalit qayta sinalmaydi, xabar takrorlanmaydi
    monkeypatch.setattr(voicelab, "_state", {"current": None, "loaded": False})
    monkeypatch.setattr(voicelab, "_exhausted", {})
    used.clear()
    run(voicelab.synthesize("a"))
    assert used == ["vlk_k2"] and len(alerts) == 1


def test_all_keys_exhausted_raises(monkeypatch, fast_sleep):
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_a,vlk_b")
    alerts = []

    def fake(*a, **kw):
        raise voicelab.VoiceLabError(402, "insufficient_credits")

    async def notify(text):
        alerts.append(text)
    monkeypatch.setattr(voicelab, "_request", fake)
    monkeypatch.setattr(voicelab, "_notify_owner", notify)
    with pytest.raises(voicelab.VoiceLabError) as ei:
        run(voicelab.synthesize("Salom"))
    assert ei.value.code == "insufficient_credits" and "barcha 2 ta" in str(ei.value)
    assert len(alerts) == 1 and "qolmadi" in alerts[-1] and "barcha 2 ta" in alerts[-1]
    with pytest.raises(voicelab.VoiceLabError):  # hammasi tugagan holat — xabar har so'rovda takrorlanmaydi
        run(voicelab.synthesize("Salom"))
    assert len(alerts) == 1


def test_other_errors_do_not_switch_key(monkeypatch, fast_sleep):
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_a,vlk_b")
    monkeypatch.setattr(voicelab, "_exhausted", {})
    used = []

    def fake(*a, **kw):
        used.append(voicelab._current_key.get())
        raise voicelab.VoiceLabError(400, "invalid_voice")
    monkeypatch.setattr(voicelab, "_request", fake)
    with pytest.raises(voicelab.VoiceLabError):
        run(voicelab.synthesize("Salom"))
    assert used == ["vlk_a"]
