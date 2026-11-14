"""api «щодня»: магазини, пошук товарів, обхід каталогу сторінками."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from common import Failed, Scan, discount, get_json, get_sync, scan, session

BASE_URL = "https://service.shchodnia.com/api/v2"
PRODUCTS_URL = f"{BASE_URL}/products"
STORES_URL = f"{BASE_URL}/shops/map"
PER_PAGE = 100

Fetch = Callable[[int], Awaitable[Any]]
Handle = Callable[[Any], tuple[int, list[Any]]]


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class Store:
    id: str
    address: str
    title: str

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Store:
        return cls(str(d.get("id") or ""), d.get("address") or "", d.get("title") or "")


@dataclass(frozen=True)
class Product:
    title: str
    sku: str
    barcode: str
    category: str
    status: str
    price: float
    promo_price: float

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Product:
        return cls(
            title=str(d.get("title") or "?"),
            sku=str(d.get("sku") or "-"),
            barcode=str(d.get("barcode") or "-"),
            category=str(d.get("category") or "-"),
            status=str(d.get("status") or "active"),
            price=_num(d.get("price")),
            promo_price=_num(d.get("promo_price")),
        )

    @property
    def on_promo(self) -> bool:
        return 0 < self.promo_price < self.price

    @property
    def current(self) -> float:
        return self.promo_price if self.on_promo else self.price

    @property
    def discount(self) -> int:
        return discount(self.price, self.promo_price) if self.on_promo else 0


def products_params(page: int, shop_id: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "per_page": PER_PAGE}
    if shop_id:
        params["shop_id"] = shop_id
    return params


def items(data: Any) -> list[dict[str, Any]]:
    rows = data.get("data", []) if isinstance(data, dict) else []
    return [r for r in rows if isinstance(r, dict)]


def get_stores() -> list[Store]:
    data = get_sync(STORES_URL)
    return [Store.from_api(d) for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def stores_matching(stores: list[Store], query: str) -> list[Store]:
    q = query.lower()
    return [s for s in stores if q in s.address.lower() or q in s.title.lower()]


def search(query: str, shop_id: str | None = None) -> tuple[Product | None, bool]:
    """(товар, точний збіг за штрихкодом чи артикулом); інакше найближчий результат."""
    params: dict[str, Any] = {"search": query}
    if shop_id:
        params["shop_id"] = shop_id
    rows = items(get_sync(PRODUCTS_URL, params))
    for r in rows:
        if str(r.get("barcode")) == query or str(r.get("sku")) == query:
            return Product.from_api(r), True
    return (Product.from_api(rows[0]), False) if rows else (None, False)


def pick_promo(data: Any) -> tuple[int, list[Product]]:
    """товари сторінки зі знижкою."""
    products = [Product.from_api(r) for r in items(data)]
    return len(products), [p for p in products if p.on_promo]


async def walk_catalog(
    fetch: Fetch,
    handle: Handle,
    *,
    workers: int = 15,
    found_label: str | None = "знижок",
) -> tuple[Scan, int]:
    """перша сторінка дає кількість сторінок, решта йде пулом. повертає (scan, товарів у каталозі)."""
    first = await fetch(1)  # Failed тут означає, що каталог недоступний
    if first is None:
        raise Failed
    meta = first.get("meta", {}) if isinstance(first, dict) else {}
    pages = int(meta.get("pages") or 1)
    total = int(meta.get("total") or 0)
    res = Scan(done=1)
    res.checked, res.found = handle(first)
    res = await scan(
        range(2, pages + 1),
        pages,
        fetch,
        handle,
        workers=workers,
        unit="сторінок",
        found_label=found_label,
        result=res,
    )
    return res, total


async def scan_discounts(shop_id: str | None, workers: int = 15) -> tuple[Scan, int]:
    async with session(workers) as s:

        def fetch(page: int) -> Awaitable[Any]:
            return get_json(s, PRODUCTS_URL, products_params(page, shop_id))

        return await walk_catalog(fetch, pick_promo, workers=workers)
