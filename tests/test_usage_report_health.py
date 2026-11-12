from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import ai
import health
import report
import runtime
import storage
import tg_client
import usage
from conftest import run

TZ = ZoneInfo("Asia/Tashkent")


# ---------- xarajat nazorati (AI_DAILY_LIMIT=5, AI_CHAT_DAILY_LIMIT=2 — conftest) ----------
def test_per_chat_limit(fake_storage):
    for _ in range(2):
        assert run(usage.check(10))[0]
        run(usage.record(10, "chat", tokens=100))
    ok, reason = run(usage.check(10))
    assert not ok and "suhbatdosh" in reason
    assert run(usage.check(11))[0]  # boshqa odamga ta'sir qilmaydi
    assert fake_storage.d_stats["ai_tokens"] == 200


def test_global_limit(fake_storage):
    for cid in range(5):
        run(usage.record(cid, "chat"))
    ok, reason = run(usage.check(99))
    assert not ok and "umumiy" in reason


def test_alert_once_per_day(monkeypatch):
    monkeypatch.setattr(usage, "_alerted", set())
    assert usage.should_alert("bu suhbatdosh uchun kunlik limit", 1)
    assert not usage.should_alert("bu suhbatdosh uchun kunlik limit", 1)
    assert usage.should_alert("bu suhbatdosh uchun kunlik limit", 2)
    assert usage.should_alert("kunlik umumiy AI limiti tugadi", 1)
    assert not usage.should_alert("kunlik umumiy AI limiti tugadi", 3)


# ---------- kunlik hisobot ----------
def test_report_empty_day():
    text = report.build_report({}, [], datetime(2026, 11, 12, tzinfo=TZ))
    assert "12-noyabr" in text and "hech kim yozmadi" in text


def test_report_full():
    stats = {"chats": [1, 2, 3], "messages": 9, "urgent": 1, "calls": 2, "spam": 1,
             "ai_requests": 6, "ai_chat": 5, "ai_voice": 1, "ai_tokens": 4200}
    pending = [{"name": "Ali <script>", "summary": "Sayt buyurtma", "urgent": True},
               {"name": "Vali", "last_text": "salom", "urgent": False}]
    text = report.build_report(stats, pending, datetime(2026, 11, 12, tzinfo=TZ))
    assert "<b>3</b> kishi (9 ta xabar)" in text
    assert "🔴 muhim: 1" in text and "📞 chaqirishgan: 2" in text and "🚫 spam: 1" in text
    assert "~4.2k token" in text
    assert "Javob kutayotganlar: 2" in text
    assert "&lt;script&gt;" in text  # HTML xavfsiz


def test_seconds_until():
    now = datetime(2026, 11, 12, 20, 0, tzinfo=TZ)
    assert report.seconds_until(now, 21, 0) == 3600
    assert report.seconds_until(now, 19, 0) == 23 * 3600  # ertaga


# ---------- holat nazorati ----------
class FakeUser:
    connected = True

    def is_connected(self):
        return self.connected


def test_health_alerts_only_on_change(fake_storage, monkeypatch):
    async def key_ok():
        return None
    monkeypatch.setattr(ai, "check_key", key_ok)
    alerts = []

    async def alert(text):
        alerts.append(text)

    user = FakeUser()
    m = health.Monitor(user, alert)
    run(m.check_once())
    assert alerts == []                                  # hammasi joyida — jim

    fake_storage.d_ping_error = ConnectionError("x")
    user.connected = False
    run(m.check_once())
    run(m.check_once())                                  # ikkinchi marta takrorlanmaydi
    assert len(alerts) == 2 and all(a.startswith("❌") for a in alerts)

    fake_storage.d_ping_error = None
    user.connected = True
    run(m.check_once())
    assert len(alerts) == 4 and alerts[-1].startswith("✅")


# ---------- runtime sozlamalar ----------
def test_runtime_update_wakes_listeners(fake_storage, monkeypatch):
    monkeypatch.setattr(runtime, "_listeners", [])
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))

    async def scenario():
        a, c = runtime.listener(), runtime.listener()
        await runtime.update(online=False)
        return a.is_set(), c.is_set()

    assert run(scenario()) == (True, True)
    assert runtime.get("online") is False
    assert fake_storage.d_runtime == {"online": False}
    with pytest.raises(KeyError):
        run(runtime.update(nomalum=1))


def test_runtime_load_ignores_unknown(fake_storage, monkeypatch):
    monkeypatch.setattr(runtime, "_values", dict(runtime.DEFAULTS))
    fake_storage.d_runtime = {"quote_mode": "off", "last_report": "2026-11-12"}
    run(runtime.load())
    assert runtime.get("quote_mode") == "off"
    assert "last_report" not in runtime._values


# ---------- maxfiy ma'lumotlar logga chiqmaydi ----------
def test_redact_secrets(monkeypatch):
    import logging
    monkeypatch.setattr(tg_client.config, "MONGODB_URI", "mongodb://user1:parol123@host/db")
    f = tg_client._RedactSecrets()
    rec = logging.LogRecord("x", logging.INFO, "", 0, "hash=%s uri=%s pw=%s",
                            ("testhash12345", "mongodb://user1:parol123@host/db", "parol123"), None)
    f.filter(rec)
    msg = rec.getMessage()
    assert "testhash12345" not in msg and "parol123" not in msg and msg.count("***") == 3


def test_today_key_format():
    assert len(storage.today_key()) == 10
