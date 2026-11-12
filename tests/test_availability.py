import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import availability as av
import calendar_ics as cal
import config
from conftest import run

TZ = ZoneInfo("Asia/Tashkent")


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(av, "_last_active_mem", 0.0)
    monkeypatch.setattr(av, "BUSY_RULES", [])
    monkeypatch.setattr(cal, "_cache", None)
    monkeypatch.setattr(config, "CALENDAR_ICS_URL", "")


# ---------- ish vaqti jadvali ----------
@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 11, 12, 10, 0), "18:00"),   # payshanba, ish vaqti
    (datetime(2026, 11, 12, 19, 0), None),      # payshanba kechqurun
    (datetime(2026, 11, 14, 11, 0), "14:00"),   # shanba
    (datetime(2026, 11, 15, 23, 0), "07:00"),   # yakshanba tun
    (datetime(2026, 11, 16, 6, 0), "07:00"),    # dushanba tong — yakshanba tunining davomi
    (datetime(2026, 11, 16, 8, 0), None),
])
def test_busy_hours(monkeypatch, when, expected):
    monkeypatch.setattr(av, "BUSY_RULES", av.parse_busy_hours(
        "Du-Ju 09:00-18:00; Shanba 10:00-14:00; Ya 22:00-07:00"))
    assert av._in_busy_hours(when.replace(tzinfo=TZ)) == expected


def test_busy_hours_english_and_invalid():
    rules = av.parse_busy_hours("Mon-Fri 09:00-18:00; noto'g'ri qoida")
    assert len(rules) == 1
    assert rules[0][0] == {0, 1, 2, 3, 4}


# ---------- rejimlar ----------
def test_modes(fake_storage):
    fake_storage.d_mode = "on"
    assert run(av.get_status()).reply is True
    fake_storage.d_mode = "off"
    st = run(av.get_status())
    assert (st.reply, st.notify) == (False, True)


def test_auto_owner_active_then_away(fake_storage):
    fake_storage.d_mode = "auto"
    assert run(av.get_status()).reply is True            # uzoq vaqt yozmagan
    run(av.mark_owner_active())
    st = run(av.get_status())
    assert (st.reply, st.notify) == (False, False)        # hozirgina yozdi — o'zi javob beradi
    av._last_active_mem = time.time() - (config.OWNER_ACTIVE_MINUTES + 1) * 60
    assert run(av.get_status()).reply is True


def test_auto_busy_hours_beat_owner_activity(fake_storage, monkeypatch):
    fake_storage.d_mode = "auto"
    monkeypatch.setattr(av, "BUSY_RULES", av.parse_busy_hours("Mon-Sun 00:00-23:59"))
    run(av.mark_owner_active())
    st = run(av.get_status())
    assert st.reply and "ish vaqti" in st.reason


# ---------- kalendar ----------
def _ics(now: datetime) -> bytes:
    f = lambda t: t.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    return f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
SUMMARY:Maxfiy uchrashuv
DTSTART:{f(now - timedelta(minutes=30))}
DTEND:{f(now + timedelta(minutes=30))}
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Davomi
DTSTART:{f(now + timedelta(minutes=33))}
DTEND:{f(now + timedelta(minutes=90))}
END:VEVENT
BEGIN:VEVENT
UID:3
SUMMARY:Keyinroq
DTSTART:{f(now + timedelta(hours=5))}
DTEND:{f(now + timedelta(hours=6))}
END:VEVENT
BEGIN:VEVENT
UID:4
SUMMARY:Tug'ilgan kun
DTSTART;VALUE=DATE:{now:%Y%m%d}
END:VEVENT
BEGIN:VEVENT
UID:5
SUMMARY:Bo'sh
TRANSP:TRANSPARENT
DTSTART:{f(now - timedelta(hours=1))}
DTEND:{f(now + timedelta(hours=8))}
END:VEVENT
END:VCALENDAR""".replace("\n", "\r\n").encode()


def test_calendar_merges_back_to_back_and_ignores_free(monkeypatch):
    now = datetime.now(TZ).replace(second=0, microsecond=0)
    monkeypatch.setattr(config, "CALENDAR_ICS_URL", "https://example.invalid/x.ics")
    monkeypatch.setattr(cal, "_fetch_sync", lambda: _ics(now))
    busy = run(cal.current_busy(now))
    assert busy is not None
    assert busy.until == now + timedelta(minutes=90)


def test_calendar_not_busy_and_network_error(monkeypatch):
    now = datetime.now(TZ)
    monkeypatch.setattr(config, "CALENDAR_ICS_URL", "https://example.invalid/x.ics")
    monkeypatch.setattr(cal, "_fetch_sync", lambda: b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    assert run(cal.current_busy(now)) is None

    def boom():
        raise OSError("tarmoq yo'q")
    monkeypatch.setattr(cal, "_cache", None)
    monkeypatch.setattr(cal, "_fetch_sync", boom)
    assert run(cal.current_busy(now)) is None  # xato — dastur yiqilmaydi


def test_auto_status_mentions_calendar_until(fake_storage, monkeypatch):
    fake_storage.d_mode = "auto"
    now = datetime.now(TZ).replace(second=0, microsecond=0)
    monkeypatch.setattr(config, "CALENDAR_ICS_URL", "https://example.invalid/x.ics")
    monkeypatch.setattr(cal, "_fetch_sync", lambda: _ics(now))
    st = run(av.get_status())
    assert st.reply
    assert f"{now + timedelta(minutes=90):%H:%M} gacha" in st.ai_context
    assert "Maxfiy uchrashuv" not in st.ai_context  # uchrashuv nomi hech qachon chiqmaydi
