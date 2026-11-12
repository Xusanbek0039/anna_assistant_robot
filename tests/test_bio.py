import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from telethon.tl.functions.account import UpdateProfileRequest

import bio_updater as b
import config
import runtime
from conftest import run

TZ = ZoneInfo("Asia/Tashkent")


# ---------- sof funksiyalar ----------
@pytest.mark.parametrize("code,icon", [
    (0, "☀️"), (1, "🌤️"), (2, "⛅"), (3, "☁️"), (45, "🌫️"), (48, "🌫️"),
    (53, "🌧️"), (63, "🌧️"), (81, "🌧️"), (73, "❄️"), (86, "❄️"), (95, "⛈️"), (99, "⛈️"),
    (1234, "🌡️"),
])
def test_weather_icon(code, icon):
    assert b.weather_icon(code)[0] == icon


@pytest.mark.parametrize("value,expected", [(17.5, 18), (18.49, 18), (-0.4, 0), (-2.5, -2), (0.5, 1)])
def test_round_temp(value, expected):
    assert b.round_temp(value) == expected


def test_tg_len_counts_emoji_as_two():
    assert b.tg_len("abc") == 3
    assert b.tg_len("🕒") == 2


# ---------- bio matni ----------
def _updater(quotes=None):
    u = b.BioUpdater(client=None, dry_run=True)
    u.quotes = quotes if quotes is not None else ["Bilim — kuch"]
    u.weather = b.Weather(7, 1, "🌤️", "x", time.time())
    return u


def test_build_bio_full_with_quote():
    u = _updater()
    text = u.build_bio(datetime(2026, 11, 12, 14, 35, tzinfo=TZ))
    assert text == "Full Stack Developer | 🕒 14:35 | 🌤️ 7°C | “Bilim — kuch”"
    assert b.tg_len(text) <= 70


def test_build_bio_never_exceeds_limit_and_keeps_prefix():
    u = _updater(quotes=["Juda uzun iqtibos bu yerda sig'maydi albatta"])
    for temp in (-25, 7, 40):
        u.weather = b.Weather(temp, 1, "🌤️", "x", time.time())
        text = u.build_bio(datetime(2026, 11, 12, 9, 5, tzinfo=TZ))
        assert b.tg_len(text) <= 70
        assert text.startswith("Full Stack Developer")
        assert "“" not in text  # sig'magan iqtibos tashlab yuboriladi


def test_build_bio_fallback_when_weather_stale():
    u = _updater()
    u.weather = b.Weather(7, 1, "🌤️", "x", time.time() - config.WEATHER_MAX_AGE_SECONDS - 10)
    assert "🌡️ --°C" in u.build_bio(datetime.now(TZ))


def test_quote_rotates_daily():
    u = _updater(quotes=["A", "B", "C"])
    day = datetime(2026, 11, 12, tzinfo=TZ)
    picks = {u.pick_quote(day + timedelta(days=i), 20) for i in range(3)}
    assert picks == {"A", "B", "C"}


def test_runtime_quote_modes(monkeypatch):
    monkeypatch.setitem(runtime._values, "quote_mode", "fixed")
    monkeypatch.setitem(runtime._values, "quote_text", "Mening iqtibosim")
    u = _updater(quotes=["Fayldagi"])
    assert "Mening iqtibosim" in u.build_bio(datetime.now(TZ))
    monkeypatch.setitem(runtime._values, "quote_mode", "off")
    assert "“" not in u.build_bio(datetime.now(TZ))


# ---------- zaxira va tiklash ----------
class FakeTG:
    def __init__(self, about="Eski bio", first="Husan", last="Suyunov"):
        self.about, self.first, self.last = about, first, last
        self.updates = []

    async def get_me(self):
        class Me:
            premium = False
            first_name = self.first
            last_name = self.last
            id = 1
        return Me()

    async def __call__(self, req):
        if isinstance(req, UpdateProfileRequest):
            self.updates.append((req.about, req.last_name))
            return True

        class Full:
            class full_user:
                about = self.about
        return Full()


@pytest.fixture
def backup_file(tmp_path, monkeypatch):
    path = tmp_path / "bio_backup.json"
    monkeypatch.setattr(config, "BIO_BACKUP_FILE", str(path))
    return path


def test_backup_saved_once_and_restore(fake_storage, backup_file, fast_sleep, monkeypatch):
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://fake")  # _safe_db fake storage'ni chaqirsin
    tg = FakeTG()
    u = b.BioUpdater(tg, dry_run=False)
    run(u.prepare())
    run(u.tick())
    about, last = tg.updates[-1]
    assert about.startswith("Full Stack Developer | 🕒")
    assert last.startswith("Suyunov 🕒")

    saved = json.loads(backup_file.read_text(encoding="utf-8"))
    assert saved["original_bio"] == "Eski bio"
    assert saved["original_last_name"] == "Suyunov"
    assert fake_storage.d_bio["original_bio"] == "Eski bio"

    # Qayta ishga tushish: Telegram'dagi bio va familiya endi avtomatik — zaxira ustidan yozilmasin
    tg2 = FakeTG(about=about, last=last)
    u2 = b.BioUpdater(tg2, dry_run=False)
    run(u2.prepare())
    assert json.loads(backup_file.read_text(encoding="utf-8"))["original_bio"] == "Eski bio"
    assert u2.orig_last == "Suyunov"

    run(u2.restore())
    assert tg2.updates[-1] == ("Eski bio", "Suyunov")


def test_dry_run_never_calls_telegram(fake_storage, backup_file, fast_sleep):
    tg = FakeTG()
    u = b.BioUpdater(tg, dry_run=True)
    run(u.prepare())
    run(u.tick())
    run(u.restore())
    assert tg.updates == []
