import pytest

import config
import tts
from conftest import run


@pytest.mark.parametrize("n,words", [
    (0, "nol"), (7, "yetti"), (15, "oʻn besh"), (100, "yuz"), (250, "ikki yuz ellik"), (1000, "ming"),
    (25000, "yigirma besh ming"), (1_250_000, "bir million ikki yuz ellik ming"),
    (2026, "ikki ming yigirma olti"),
])
def test_number_words(n, words):
    assert tts.number_words(n) == words


def test_ordinals():
    assert tts.ordinal_words(2) == "ikkinchi"
    assert tts.ordinal_words(14) == "oʻn toʻrtinchi"
    assert tts.ordinal_words(6) == "oltinchi"


@pytest.mark.parametrize("src,expected", [
    ("taxminan 15:00 dan keyin bo'shaydi", "taxminan oʻn beshdan keyin boʻshaydi"),
    ("Narxi 1 250 000 so'm", "Narxi bir million ikki yuz ellik ming soʻm"),
    ("50% oldindan", "ellik foiz oldindan"),
    ("1.5 mln", "bir yarim million"),
    ("14-noyabr", "oʻn toʻrtinchi noyabr"),
    ("15.11.2026 da", "oʻn beshinchi noyabr ikki ming yigirma olti yilda"),
    ("Ma'lumot, g‘alaba", "Maʼlumot, gʻalaba"),
    ("Salom 😊 https://x.uz *muhim*", "Salom havola muhim"),
    ("Ҳусан ака ҳозир банд", "Husan aka hozir band"),
])
def test_prepare_text_uz(src, expected):
    assert tts.prepare_text(src, "uz") == expected


def test_detect_lang():
    assert tts.detect_lang("Husan aka hozir band") == "uz"
    assert tts.detect_lang("Хусан сейчас занят") == "ru"
    assert tts.detect_lang("Ҳусан ака банд") == "uz"
    assert tts.detect_lang("Hello, what can I help you with?") == "en"


def test_prepare_text_keeps_russian():
    assert tts.prepare_text("Хусан занят 😊", "ru") == "Хусан занят"


def test_synthesize_voicelab_only(monkeypatch):
    import voicelab
    used = []

    async def vl(text, lang):
        used.append((text, lang))
        return b"OggS..."
    monkeypatch.setattr(config, "VOICELAB_API_KEY", "vlk_test")
    monkeypatch.setattr(tts, "_voicelab", vl)
    assert run(tts.synthesize("Narxi 25 000 so'm")) == (b"OggS...", "voicelab")
    assert run(tts.synthesize("Здравствуйте, Хусан занят"))[1] == "voicelab"
    assert used[0] == ("Narxi yigirma besh ming soʻm", "uz")
    assert used[1][1] == "ru"

    monkeypatch.setattr(config, "VOICELAB_API_KEY", "")
    with pytest.raises(voicelab.VoiceLabError):
        run(tts.synthesize("Salom"))


def test_synthesize_empty_text():
    with pytest.raises(ValueError):
        run(tts.synthesize("😊😊"))
