"""щодня: пошук товару і сканування знижок у каталозі вибраного магазину."""

from __future__ import annotations

import asyncio

import requests

from common import (
    Config,
    Failed,
    accent,
    ask_number,
    clear_screen,
    dim,
    failed_line,
    money,
    table,
)
from shchodnya_api import Product, Store, get_stores, scan_discounts, search, stores_matching

config = Config("config_shchodnya.json")
TOP = 20


def select_store() -> Store | None:
    print(dim("завантажую магазини…"))
    try:
        stores = get_stores()
    except (requests.RequestException, ValueError) as e:
        print("не вдалося отримати магазини:", e)
        return None
    if not stores:
        print("список магазинів порожній")
        return None
    query = input(f"місто або вулиця ({len(stores)} магазинів): ").strip()
    found = stores_matching(stores, query)
    if not found:
        print("магазинів не знайдено")
        return None
    for i, s in enumerate(found, 1):
        print(f"{i:3d}  {s.address} {dim(f'({s.title}, id {s.id})')}")
    return found[ask_number("номер: ", 1, len(found)) - 1]


def print_product(p: Product) -> None:
    price = money(p.current)
    if p.on_promo:
        price += "  " + accent(f"-{p.discount}%") + f", було {money(p.price)}"
    print()
    print(p.title)
    print(f"  ціна       {price}")
    print(f"  артикул    {p.sku}")
    print(f"  штрихкод   {p.barcode}")
    print(f"  категорія  {p.category}")
    print(f"  статус     {p.status}")


def search_loop(shop_id: str | None) -> None:
    while True:
        query = input("\nштрихкод, артикул або назва (порожньо — назад): ").strip()
        if not query or query.lower() == "exit":
            return
        try:
            p, exact = search(query, shop_id)
        except (requests.RequestException, ValueError) as e:
            print("помилка запиту:", e)
            continue
        if p is None:
            print("товар не знайдено")
            continue
        if not exact:
            print(dim("точного збігу немає, найближчий результат:"))
        print_product(p)


def run_scan(shop_id: str | None) -> None:
    print()
    try:
        res, total = asyncio.run(scan_discounts(shop_id))
    except Failed:
        print("каталог не відповідає, спробуйте пізніше")
        return
    found = sorted(res.found, key=lambda p: p.discount, reverse=True)
    print(f"перевірено товарів: {res.checked} з {total}, знижок: {len(found)}")
    if found:
        print()
        rows = [
            [f"-{p.discount}%", money(p.promo_price), money(p.price), p.sku, p.barcode, p.title]
            for p in found[:TOP]
        ]
        print(table(["знижка", "ціна", "було", "код", "штрихкод", "назва"], rows))
    if res.failed:
        print()
        print(failed_line("сторінок", res.failed))


def main() -> None:
    clear_screen()
    cfg = config.load()
    shop_id = cfg.get("shop_id")
    shop_name = cfg.get("shop_name", "")

    if not shop_id:
        store = select_store()
        if not store:
            return
        shop_id, shop_name = store.id, store.address
        config.save(shop_id=shop_id, shop_name=shop_name)

    while True:
        print()
        print(f"щодня  {dim(shop_name)}")
        print("  1  пошук товару")
        print("  2  сканувати знижки")
        print("  3  змінити магазин")
        print("  0  вихід")
        choice = input("> ").strip()

        if choice == "1":
            search_loop(str(shop_id))
        elif choice == "2":
            run_scan(str(shop_id))
        elif choice == "3":
            store = select_store()
            if store:
                shop_id, shop_name = store.id, store.address
                config.save(shop_id=shop_id, shop_name=shop_name)
        elif choice == "0":
            return
        else:
            print("такого пункту немає")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
