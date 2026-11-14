"""дамп каталогу «щодня» в локальну базу sqlite (таблиця products)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import Any, Awaitable

from common import Failed, dim, failed_line, get_json, session
from shchodnya_api import PRODUCTS_URL, items, products_params, walk_catalog

DB_FILE = "shchodnya.sqlite"
WORKERS = 15

COLUMNS = (
    "id", "sku", "barcode", "title", "description", "category", "price", "created_at",
    "image", "promo_title", "promo_start_at", "promo_end_at", "promo_price",
    "promo_type", "promo_price_type", "status", "price_mobile", "barcodes",
)
TYPES = {"price": "REAL", "promo_price": "REAL", "price_mobile": "REAL"}

CREATE = "CREATE TABLE IF NOT EXISTS products (\n    {}\n)".format(
    ",\n    ".join(
        f"{c} {'TEXT PRIMARY KEY' if c == 'id' else TYPES.get(c, 'TEXT')}" for c in COLUMNS
    )
)
INSERT = "INSERT OR REPLACE INTO products ({}) VALUES ({})".format(
    ", ".join(COLUMNS), ", ".join("?" * len(COLUMNS))
)


def init_db(path: str = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(CREATE)
    conn.commit()
    return conn


def record(p: dict[str, Any]) -> tuple[Any, ...]:
    """рядок для таблиці; barcodes зберігається як json-рядок."""
    return tuple(
        json.dumps(p.get("barcodes", []), ensure_ascii=False) if c == "barcodes" else p.get(c)
        for c in COLUMNS
    )


def saver(conn: sqlite3.Connection):
    """handle для walk_catalog: пише сторінку в базу, повертає (скільки записано, [])."""

    def handle(data: Any) -> tuple[int, list[Any]]:
        rows = [record(p) for p in items(data)]
        if rows:
            conn.executemany(INSERT, rows)
            conn.commit()
        return len(rows), []

    return handle


async def dump(conn: sqlite3.Connection, workers: int = WORKERS):
    async with session(workers) as s:

        def fetch(page: int) -> Awaitable[Any]:
            return get_json(s, PRODUCTS_URL, products_params(page), timeout=15)

        return await walk_catalog(fetch, saver(conn), workers=workers, found_label=None)


def main() -> None:
    print(f"дамп каталогу щодня у {DB_FILE}")
    started = time.monotonic()
    conn = init_db()
    try:
        res, total = asyncio.run(dump(conn))
    except Failed:
        print("каталог не відповідає, спробуйте пізніше")
        return
    finally:
        conn.close()
    mins, secs = divmod(int(time.monotonic() - started), 60)
    print(f"збережено товарів: {res.checked} з {total} {dim(f'за {mins} хв {secs} с')}")
    if res.failed:
        print(failed_line("сторінок", res.failed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
