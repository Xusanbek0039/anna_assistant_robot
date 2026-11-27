"""Drive'dagi zaxiradan MongoDB'ni tiklash.

1. Google Drive -> «Anna zaxira» -> kerakli sana papkasini kompyuterga yuklab oling (zip bo'lsa — oching).
2. python restore_backup.py <papka> --yes             # barcha kolleksiyalar
   python restore_backup.py <papka> --yes --only archive,history

Hujjatlar _id bo'yicha yoziladi (bor bo'lsa — zaxiradagi bilan almashtiriladi, yo'q bo'lsa — qo'shiladi).
Bazadagi zaxirada yo'q yozuvlar O'CHIRILMAYDI.
"""
import argparse
import asyncio
import gzip
import os
import sys

from bson import json_util
from pymongo import ReplaceOne

import storage

BATCH = 500


async def restore_file(path: str, name: str) -> int:
    coll = storage.raw_collection(name)
    ops, n = [], 0
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            doc = json_util.loads(line)
            ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
            if len(ops) >= BATCH:
                await coll.bulk_write(ops, ordered=False)
                n += len(ops)
                ops = []
    if ops:
        await coll.bulk_write(ops, ordered=False)
        n += len(ops)
    return n


def find_files(folder: str, only: set[str] | None) -> list[tuple[str, str]]:
    out = []
    for fname in sorted(os.listdir(folder)):
        if fname.endswith(".jsonl.gz"):
            name = fname[: -len(".jsonl.gz")]
            if name != "locks" and (not only or name in only):
                out.append((os.path.join(folder, fname), name))
    return out


async def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description="Anna: Drive zaxirasidan MongoDB'ni tiklash")
    ap.add_argument("folder", help="yuklab olingan zaxira papkasi (ichida *.jsonl.gz)")
    ap.add_argument("--yes", action="store_true", help="tasdiqlash — busiz hech narsa yozilmaydi")
    ap.add_argument("--only", default="", help="faqat shu kolleksiyalar, vergul bilan: archive,history")
    args = ap.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    files = find_files(args.folder, only)
    if not files:
        sys.exit("Papkada .jsonl.gz fayl topilmadi")
    print("Tiklanadi:", ", ".join(n for _, n in files))
    if not args.yes:
        sys.exit("Tasdiqlash uchun --yes qo'shing (bazadagi bir xil _id li yozuvlar almashtiriladi).")
    if not await storage.init(retry_forever=False):
        sys.exit("MongoDB'ga ulanib bo'lmadi (.env dagi MONGODB_URI)")
    for path, name in files:
        print(f"{name}: {await restore_file(path, name)} ta yozuv tiklandi")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
