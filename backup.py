"""MongoDB bazasini har kuni Google Drive'ga zaxiralash: <asosiy papka>/Anna zaxira/YYYY-MM-DD/<kolleksiya>.jsonl.gz

Har bir kolleksiya qatorma-qator (oqim bilan) siqilgan faylga yoziladi — katta arxiv ham xotiraga to'liq
yuklanmaydi. Eski zaxiralar BACKUP_KEEP_DAYS kundan keyin Drive savatiga tashlanadi.
Tiklash: `python restore_backup.py <yuklab olingan papka> --yes`
"""
import asyncio
import gzip
import logging
import os
import shutil
import tempfile
from datetime import date, timedelta

from bson import json_util

import config
import gdrive
import runtime
import schedule
import storage

log = logging.getLogger("backup")

FOLDER = "Anna zaxira"
# locks — vaqtinchalik qulf; instagram — Instagram sessiyasi (akkauntga to'liq kirish beradi, Drive'ga chiqmasin)
SKIP = {"locks", "instagram"}
JSON_OPTS = json_util.CANONICAL_JSON_OPTIONS  # ObjectId, Binary, sana turlari aynan tiklanadi


def folder_link(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


async def dump_collection(name: str, path: str) -> int:
    """Kolleksiyani .jsonl.gz faylga yozadi. Hujjatlar soni."""
    n = 0
    with gzip.open(path, "wt", encoding="utf-8") as f:
        async for doc in storage.raw_collection(name).find({}):
            f.write(json_util.dumps(doc, json_options=JSON_OPTS))
            f.write("\n")
            n += 1
    return n


async def cleanup(root: str, keep_days: int, today: date) -> int:
    """Eski sana papkalarini savatga tashlaydi. Tashlanganlar soni."""
    cutoff = today - timedelta(days=keep_days)
    removed = 0
    for item in await gdrive.list_children(root, folders_only=True):
        try:
            day = date.fromisoformat(item["name"])
        except ValueError:
            continue  # zaxira papkasi emas — tegmaymiz
        if day < cutoff:
            await gdrive.trash(item["id"])
            removed += 1
    return removed


async def run_backup() -> dict:
    """Zaxira oladi. {"date", "collections", "docs", "bytes", "link", "removed"}"""
    if not gdrive.enabled():
        raise RuntimeError("Google Drive sozlanmagan: " + ", ".join(gdrive.missing()))
    today = schedule.now().date()
    root = await gdrive.ensure_folder(FOLDER)
    day_folder = await gdrive.ensure_folder(today.isoformat(), parent=root)
    names = [n for n in await storage.collection_names()
             if n not in SKIP and (config.BACKUP_MEDIA or n != "archive_media")]
    # bir kunda qayta olinsa — o'sha kungi fayllar yangilanadi (nusxalar ko'paymaydi)
    existing = {f["name"]: f["id"] for f in await gdrive.list_children(day_folder)}
    tmp = tempfile.mkdtemp(prefix="anna_backup_")
    total_docs = total_bytes = 0
    try:
        for name in names:
            fname = f"{name}.jsonl.gz"
            path = os.path.join(tmp, fname)
            total_docs += await dump_collection(name, path)
            total_bytes += os.path.getsize(path)
            await gdrive.upload(fname, mime="application/gzip", parent=day_folder, path=path,
                                file_id=existing.get(fname))
            os.remove(path)  # disk to'lmasin — har bir fayl yuklangach o'chiriladi
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    removed = await cleanup(root, config.BACKUP_KEEP_DAYS, today)
    log.info("Zaxira: %d ta kolleksiya, %d ta hujjat, %.1f MB", len(names), total_docs, total_bytes / 1048576)
    return {"date": today.isoformat(), "collections": len(names), "docs": total_docs, "bytes": total_bytes,
            "link": folder_link(day_folder), "removed": removed}


def describe(res: dict) -> str:
    text = (f"💾 Zaxira tayyor ({res['date']}): {res['collections']} ta kolleksiya, {res['docs']} ta yozuv, "
            f"{res['bytes'] / 1048576:.1f} MB.")
    if res.get("removed"):
        text += f" Eski zaxiralardan {res['removed']} tasi savatga tashlandi."
    return text


_lock = asyncio.Lock()


async def backup_now() -> dict:
    """Bir vaqtda faqat bitta zaxira (tugma qayta-qayta bosilsa ham)."""
    async with _lock:
        return await run_backup()


async def run(alert) -> None:
    """Har kuni BACKUP_TIME da. Xato bo'lsa — egasiga sababi bilan xabar."""
    async def job():
        try:
            await backup_now()
        except Exception as e:
            log.exception("Zaxira olinmadi")
            if not gdrive.is_auth_error(e):
                await alert(f"💾❌ Bazani Drive'ga zaxiralab bo'lmadi: {gdrive.explain(e)}")

    await schedule.daily("Bazani zaxiralash", config.BACKUP_TIME, "last_backup", job,
                         enabled=lambda: runtime.get("drive_backup") and gdrive.enabled())
