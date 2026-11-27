"""📢 Kanallar arxivi: faqat belgilangan kanallar postlari arxivga tushadi (channels.py + archive.py)."""
import pytest
from telethon.tl.types import Channel, ChatPhotoEmpty

import channels
import storage
from conftest import run
from test_archive import Ev, Msg, arc  # noqa: F401  (arc — fixture)


class Store:
    def __init__(self):
        self.rows: dict[int, str] = {}

    async def channel_archive_list(self):
        return [{"chat_id": k, "title": v} for k, v in self.rows.items()]

    async def channel_archive_add(self, chat_id, title):
        self.rows[chat_id] = title

    async def channel_archive_remove(self, chat_id):
        self.rows.pop(chat_id, None)


@pytest.fixture
def chn(monkeypatch):
    st = Store()
    for name in ("channel_archive_list", "channel_archive_add", "channel_archive_remove"):
        monkeypatch.setattr(storage, name, getattr(st, name), raising=False)
    monkeypatch.setattr(channels, "_enabled", {})
    monkeypatch.setattr(channels, "_cache", {})
    return st


def _channel(cid):
    return Channel(id=cid, title=f"Kanal {cid}", photo=ChatPhotoEmpty(), date=None, broadcast=True)


class ChEv(Ev):
    """Kanal posti: is_channel=True, is_group=False."""

    def __init__(self, msg, entity):
        super().__init__(msg, entity, private=False, group=False)
        self.is_channel = True


def test_only_selected_channels_are_archived(arc, chn):  # noqa: F811
    new = arc.user.handlers["archive_new"]
    sel, other = _channel(11), _channel(22)
    run(channels.toggle(-11, "Kanal 11"))
    run(new(ChEv(Msg("yangilik", chat=11, channel=True), sel)))
    run(new(ChEv(Msg("boshqa", chat=22, channel=True), other)))
    assert [d["text"] for d in arc.msgs.values()] == ["yangilik"]
    assert arc.chats[-11]["kind"] == "channel" and arc.chats[-11]["title"] == "Kanal 11"

    run(channels.toggle(-11))                                   # o'chirildi — yangi postlar yozilmaydi
    run(new(ChEv(Msg("keyingi", chat=11, channel=True), sel)))
    assert [d["text"] for d in arc.msgs.values()] == ["yangilik"]   # avvalgisi arxivda qoladi


def test_toggle_saved_and_loaded(chn):
    assert run(channels.toggle(-11, "Yangiliklar")) is True
    channels._enabled.clear()
    run(channels.load())
    assert channels.is_enabled(-11) and chn.rows == {-11: "Yangiliklar"}


def test_groups_are_not_channels():
    sg = type("D", (), {"is_channel": True, "is_group": True})()   # superguruh
    ch = type("D", (), {"is_channel": True, "is_group": False})()
    assert channels.is_broadcast(ch) and not channels.is_broadcast(sg)


def test_panel(chn):
    run(channels.toggle(-105, "Kanal 5"))
    items = [{"id": -100 - i, "title": f"Kanal {i}", "username": None} for i in range(12)]
    text, rows = channels.panel(items, 0)
    assert "12 ta" in text and "arxivlanyapti: <b>1</b>" in text
    assert rows[0][0] == ("✅ Kanal 5", "chn:t:-105:0:1")
    assert ("➡️", "chn:p:1:1") in rows[-3] and ("⬅️ Arxiv", "arc:panel") in rows[-1]
    assert rows[-2] == [("🗂 Arxivdagi kanallar — o'qish/yuklash", "chn:saved:1")]
    assert "to'xtatilgan" in channels.panel(items, 0, archive_on=False)[0]


class Rec:
    """2-akkaunt client'i o'rniga: ishlovchilarni yig'adi."""

    def __init__(self):
        self.handlers = []

    def on(self, ev):
        def deco(fn):
            self.handlers.append((ev, fn))
            return fn
        return deco


def test_second_account_channel_archived_and_media_via_it(arc, chn, monkeypatch):  # noqa: F811
    import archive
    monkeypatch.setattr(archive, "_clients", {})
    monkeypatch.setattr(archive, "_chat_via", {})

    async def chat_set(cid, **f):
        if cid in arc.chats:          # haqiqiy baza kabi: upsert yo'q
            arc.chats[cid].update(f)

    monkeypatch.setattr(storage, "archive_chat_set", chat_set)
    c2 = Rec()
    archive.setup_channels(c2, 2)
    new = c2.handlers[0][1]
    run(channels.toggle(-33, "Kanal 33"))
    run(new(ChEv(Msg("faqat 2-akkauntda", chat=33, channel=True), _channel(33))))
    assert [d["text"] for d in arc.msgs.values()] == ["faqat 2-akkauntda"]
    assert arc.chats[-33]["via"] == 2
    archive._chat_via.clear()                                     # qayta ishga tushgandek — bazadan o'qiladi
    assert run(archive.client_for(-33, "acc1")) is c2
    assert run(archive.client_for(5, "acc1")) == "acc1"           # oddiy chatlar — 1-akkaunt
