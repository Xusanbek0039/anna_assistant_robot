import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

import config
import control_bot
import finance
from conftest import run


def _row(kind, amount, cat="boshqa", day="2026-11-14", note="", person=None, cur="UZS"):
    return {"type": kind, "amount": amount, "category": cat, "day": day, "note": note,
            "person": person, "currency": cur, "ts": 0}


# ---------- summalar ----------
def test_fmt_sum():
    assert finance.fmt_sum(25000) == "25 000 so'm"
    assert finance.fmt_sum(-1500000) == "-1 500 000 so'm"
    assert finance.fmt_sum(12.5, "USD") == "12 USD" or finance.fmt_sum(12.5, "USD") == "13 USD"


# ---------- AI'siz tahlil ----------
def test_local_parse_expenses():
    res = finance.local_parse("bugun nonga 25 ming zaprafkaga 30 ming ishlatdim")
    assert res["finance"] and not res["ai"]
    assert [(i["type"], i["amount"], i["category"]) for i in res["items"]] == [
        ("expense", 25000, "oziq-ovqat"), ("expense", 30000, "transport")]


def test_local_parse_income_and_units():
    res = finance.local_parse("Alidan 1.5 mln oldim")
    assert res["items"][0]["type"] == "income" and res["items"][0]["amount"] == 1_500_000
    assert finance.local_parse("kecha taksiga 18 ming")["items"][0]["date_offset"] == -1
    assert finance.local_parse("Validan 100 ming qarz oldim")["items"][0]["type"] == "debt_taken"


def test_local_parse_not_finance():
    assert finance.local_parse("salom, qalaysan?")["finance"] is False
    assert finance.local_parse("2 ta non")["items"] == []  # kichik son — summa emas


def test_clean_items_rejects_bad():
    items = finance.clean_items([
        {"type": "expense", "amount": 25000, "category": "Oziq", "note": "non"},
        {"type": "nimadir", "amount": 5},
        {"type": "income", "amount": "abc"},
        {"type": "expense", "amount": -5},
        {"type": "income", "amount": 1e6, "category": "xyz", "person": "Ali", "date_offset": 5},
    ])
    assert len(items) == 2
    assert items[0]["category"] == "oziq-ovqat"
    assert items[1]["category"] == "boshqa" and items[1]["person"] == "Ali" and items[1]["date_offset"] == 0


def test_parse_uses_ai(monkeypatch):
    import ai

    payload = {"finance": True, "items": [
        {"type": "expense", "amount": 25000, "category": "oziq-ovqat", "note": "non"},
        {"type": "income", "amount": 200000, "category": "boshqa", "note": "Alidan", "person": "Ali"}]}

    class Completions:
        async def create(self, **kw):
            assert kw["response_format"] == {"type": "json_object"}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                                   usage=SimpleNamespace(total_tokens=123))

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(ai, "_get_client", lambda: client)
    res = run(finance.parse("nonga 25 ming, Alidan 200 ming oldim"))
    assert res["finance"] and res["tokens"] == 123 and len(res["items"]) == 2


def test_make_docs_offset():
    when = datetime(2026, 11, 14, 12, tzinfo=finance._TZ)
    docs = finance.make_docs([{"type": "expense", "amount": 1000, "category": "kafe", "date_offset": -1}],
                             "voice", "kecha kofe 1000", when)
    assert docs[0]["day"] == "2026-11-13" and docs[0]["source"] == "voice"


# ---------- hisobotlar ----------
def test_build_report_totals_and_categories():
    rows = [_row("expense", 25000, "oziq-ovqat", note="non"), _row("expense", 75000, "transport", note="benzin"),
            _row("income", 200000, person="Ali"), _row("debt_taken", 50000, person="Vali")]
    text = finance.build_report(rows, "Bugungi moliya", date(2026, 11, 14), date(2026, 11, 14))
    assert "200 000 so'm" in text and "100 000 so'm" in text   # kirim, chiqim
    assert "+100 000 so'm" in text                            # balans
    assert "transport: 75 000 so'm — 75%" in text
    assert "Ali" in text and "qarz oldim" in text
    assert "Eng katta xarajat: benzin" in text
    assert "Yozuvlar:" in text                                # 1 kunlik — ro'yxat bilan


def test_build_report_compare_and_empty():
    cur = [_row("expense", 150000)]
    prev = [_row("expense", 100000, day="2026-11-10")]
    text = finance.build_report(cur, "3 kunlik", date(2026, 11, 12), date(2026, 11, 14), prev_rows=prev)
    assert "↑ 50%" in text and "Kunlik o'rtacha chiqim: 50 000" in text
    assert "hech narsa yozilmagan" in finance.build_report([], "X", date(2026, 11, 14), date(2026, 11, 14))


def test_debts_balance():
    rows = [_row("debt_taken", 100000, person="ali"), _row("repay_out", 40000, person="Ali"),
            _row("debt_given", 50000, person="Vali"), _row("repay_in", 50000, person="Vali")]
    i_owe, owed = finance.debts(rows)
    assert i_owe == {"Ali": 60000} and owed == {}


def test_budget_alerts_cross_thresholds():
    budget = {"kafe": 100000, "jami": 1000000}
    before = [_row("expense", 70000, "kafe")]
    after = before + [_row("expense", 15000, "kafe")]
    assert any("85%" in a for a in finance.budget_alerts(budget, before, after))
    after2 = after + [_row("expense", 20000, "kafe")]
    assert any("tugadi" in a for a in finance.budget_alerts(budget, after, after2))
    assert finance.budget_alerts(budget, after2, after2) == []  # qayta ogohlantirmaydi


def test_period_ranges():
    t = date(2026, 11, 14)
    assert finance.period_range("3", t) == (date(2026, 11, 12), t, date(2026, 11, 9), date(2026, 11, 11))
    assert finance.period_range("month", t)[:3] == (date(2026, 11, 1), t, date(2026, 10, 1))


def test_due_reports(monkeypatch):
    monkeypatch.setattr(config, "FINANCE_WEEK_DAY", 7)
    sunday = date(2026, 11, 15)
    assert "7" in finance.due_reports(sunday)
    assert "month" in finance.due_reports(date(2026, 11, 30))
    days3 = [d for d in range(1, 31) if "3" in finance.due_reports(date(2026, 11, d))]
    assert len(days3) == 10  # har 3 kunda


def test_csv_has_bom_and_signs():
    data = finance.to_csv([_row("expense", 25000, "oziq-ovqat", note="non")]).decode("utf-8")
    assert data.startswith("﻿Sana") and "-25000" in data


# ---------- bot oqimi ----------
class _Ev:
    def __init__(self):
        self.sent = []

    async def respond(self, text, **kw):
        self.sent.append((text, kw))


def test_fin_record_saves_and_confirms(fake_storage, monkeypatch):
    async def fail(text):
        raise RuntimeError("AI yo'q")
    monkeypatch.setattr(finance, "parse", fail)  # AI ishlamasa — taxminiy tahlil
    ev = _Ev()
    assert run(control_bot._fin_record(ev, "nonga 25 ming zapravkaga 30 ming ishlatdim", "text"))
    assert [r["amount"] for r in fake_storage.d_tx] == [25000, 30000]
    text, kw = ev.sent[0]
    assert "Yozildi" in text and "55 000" in text and "taxminiy" in text
    datas = [getattr(b, "data", None) or b.type.data for row in kw["buttons"] for b in row]
    assert all(len(d) <= 64 for d in datas)
    undo = next(d for d in datas if d.startswith(b"fin:undo:"))
    run(fake_storage.delete_batch(undo.decode().split(":")[2]))
    assert fake_storage.d_tx == []


def test_fin_record_ignores_plain_text(fake_storage):
    ev = _Ev()
    assert run(control_bot._fin_record(ev, "salom, ishlar qalay?", "text")) is False
    assert ev.sent == [] and fake_storage.d_tx == []


def test_budget_amount_parsing():
    assert control_bot._parse_budget_amount("2000000") == 2_000_000
    assert control_bot._parse_budget_amount("2mln") == 2_000_000
    assert control_bot._parse_budget_amount("abc") == 0


def test_make_report_and_balance(fake_storage):
    today = finance.now().date()
    fake_storage.d_tx = [{**_row("expense", 30000, "transport", day=finance.day_key(today)), "id": "1",
                          "batch": "b"},
                         {**_row("debt_given", 100000, person="Ali", day=finance.day_key(today)), "id": "2",
                          "batch": "b"}]
    assert "30 000" in run(finance.make_report("today"))
    bal = run(finance.make_balance())
    assert "Menga qarzdor" in bal and "Ali: 100 000" in bal


@pytest.mark.parametrize("name", ["today", "3", "7", "month"])
def test_fin_panel_reports(fake_storage, name):
    assert "📒" in run(finance.make_report(name))
