"""saver: havolalarni ajratish va Saqlangan xabarlarga joylash (tarmoqsiz)."""
import os

import pytest
from telethon.tl.types import PeerChannel

import saver
from conftest import run


def test_parse_private_and_public_links():
    links = saver.parse_links("mana https://t.me/c/1234567890/15 va t.me/durov/42?single , "
                              "https://t.me/c/1234567890/7/99 https://t.me/joinchat/abc/1 https://t.me/c/1234567890/15")
    assert [(x.peer, x.msg_id, x.single) for x in links] == [
        (PeerChannel(1234567890), 15, False), ("durov", 42, True), (PeerChannel(1234567890), 99, False)]
    assert saver.parse_links("oddiy matn, havola yo'q") == []
    assert saver.parse_links("https://t.me/durov") == []


class FakeFile:
    size, mime_type = 10, "video/mp4"


class FakeMsg:
    def __init__(self, id, media="M", text="", grouped_id=None):
        self.id, self.media, self.message, self.grouped_id = id, media, text, grouped_id
        self.entities = None
        self.file = FakeFile()
        self.video = bool(media)
        self.video_note = self.gif = self.voice = self.document = self.sticker = None

    async def download_media(self, file=None, thumb=None, progress_callback=None):
        if thumb is not None:
            return None
        path = file + "v.mp4"
        with open(path, "wb") as f:
            f.write(b"data")
        if progress_callback:
            await progress_callback(4, 4)
        return path


class FakeUser:
    def __init__(self, msgs, protected=False):
        self.msgs = {m.id: m for m in msgs}
        self.protected, self.files, self.texts = protected, [], []

    async def get_input_entity(self, peer):
        return peer

    async def get_messages(self, entity, ids=None):
        if isinstance(ids, list):
            return [self.msgs.get(i) for i in ids]
        return self.msgs.get(ids)

    async def send_file(self, chat, file, **kw):
        if self.protected and not str(file).endswith(".mp4"):  # faqat yuklab olingan fayl o'tadi
            raise RuntimeError("ChatForwardsRestricted")
        self.files.append((chat, file, kw))

    async def send_message(self, chat, text, **kw):
        self.texts.append((chat, text))


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(saver, "_active", 0)
    monkeypatch.setattr(saver, "_last_done", 0.0)


def _link(msg_id, single=False):
    return saver.Link(PeerChannel(1), msg_id, single, f"https://t.me/c/1/{msg_id}")


def test_fast_copy_to_saved_messages():
    user = FakeUser([FakeMsg(5, text="izoh")])
    assert not saver.is_saving()
    assert run(saver.save_link(user, _link(5))).startswith("✅")
    chat, file, kw = user.files[0]
    assert chat == "me" and file == "M" and kw["caption"] == "izoh"
    assert saver.is_saving()                       # hozirgina tugadi — userbot buni egasi yozgani demaydi


def test_protected_channel_is_downloaded_and_reuploaded():
    user = FakeUser([FakeMsg(5, text="izoh")], protected=True)
    seen = []

    async def status(text):
        seen.append(text)

    assert run(saver.save_link(user, _link(5), status)).startswith("✅")
    chat, file, kw = user.files[0]
    assert chat == "me" and file.endswith("v.mp4") and kw["supports_streaming"] is True
    assert seen and "100%" in seen[0]
    assert not os.path.exists(file)                # vaqtinchalik fayl o'chirildi


def test_album_saved_whole_unless_single():
    msgs = [FakeMsg(4, grouped_id=7), FakeMsg(5, grouped_id=7), FakeMsg(6, grouped_id=7), FakeMsg(7)]
    user = FakeUser(msgs)
    assert "3 ta media" in run(saver.save_link(user, _link(5)))
    assert user.files[0][1] == ["M", "M", "M"]
    user = FakeUser(msgs)
    run(saver.save_link(user, _link(5, single=True)))
    assert user.files[0][1] == "M"


def test_text_only_missing_and_too_big(monkeypatch):
    user = FakeUser([FakeMsg(5, media=None, text="faqat matn")])
    assert "matni saqlandi" in run(saver.save_link(user, _link(5)))
    assert user.texts == [("me", "faqat matn")]
    assert run(saver.save_link(user, _link(99))).startswith("❌ Xabar topilmadi")
    monkeypatch.setattr(saver, "SAVE_MAX_MB", 0)
    user = FakeUser([FakeMsg(5)], protected=True)
    assert "juda katta" in run(saver.save_link(user, _link(5)))
