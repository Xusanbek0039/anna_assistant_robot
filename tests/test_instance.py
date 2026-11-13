"""instance: bir vaqtda faqat bitta nusxa ishlashi."""
import asyncio

import instance
from conftest import run


def test_first_instance_starts_immediately(fake_storage, fast_sleep):
    run(instance.wait_until_only())
    assert fake_storage.d_lock == instance.INSTANCE_ID


def test_second_instance_waits_until_first_stops(fake_storage, fast_sleep):
    fake_storage.d_lock = "boshqa-nusxa"

    async def scenario():
        waiter = asyncio.create_task(instance.wait_until_only())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not waiter.done()                   # eski nusxa tirik — kutyapti
        await fake_storage.release_lock("boshqa-nusxa")
        await asyncio.wait_for(waiter, timeout=2)

    run(scenario())
    assert fake_storage.d_lock == instance.INSTANCE_ID


def test_heartbeat_stops_when_lock_is_taken(fake_storage, fast_sleep):
    lost = []

    async def on_lost():
        lost.append(True)

    fake_storage.d_lock = "boshqa-nusxa"
    run(asyncio.wait_for(instance.heartbeat(on_lost), timeout=2))
    assert lost == [True]


def test_release_frees_lock(fake_storage):
    run(instance.wait_until_only())
    run(instance.release())
    assert fake_storage.d_lock is None
