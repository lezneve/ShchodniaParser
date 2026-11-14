"""спільне для парсера: колір, конфіг, ввід, запити з повтором, сканування, таблиця."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Sequence

import aiohttp
import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}
ATTEMPTS = 3
MAX_WAIT = 30.0


# колір: лише два акценти, вимикається через NO_COLOR або коли вивід не в термінал

_COLOR = "NO_COLOR" not in os.environ and sys.stdout.isatty()


def accent(text: str) -> str:
    return f"\033[32m{text}\033[0m" if _COLOR else text


def dim(text: str) -> str:
    return f"\033[90m{text}\033[0m" if _COLOR else text


# конфіг і ввід

class Config:
    """невеликий json-файл із вибором користувача."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, **values: Any) -> None:
        data = self.load() | values
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def ask_number(prompt: str, low: int, high: int) -> int:
    """питає число в межах [low, high], поки не отримає коректне."""
    while True:
        answer = input(prompt).strip()
        if answer.isdigit() and low <= int(answer) <= high:
            return int(answer)
        print("потрібне число від", low, "до", high)


# ціни

def discount(old: float, new: float) -> int:
    """знижка у відсотках, округлена до найближчого цілого: 100 → 80 дає 20."""
    if old <= 0 or new >= old:
        return 0
    return round((old - new) / old * 100)


def money(value: float) -> str:
    return f"{value:.2f}"


# запити

class Failed(Exception):
    """відповіді немає після всіх спроб (429, 5xx, таймаут, обрив, битий json)."""


def get_sync(url: str, params: dict[str, Any] | None = None, timeout: float = 10) -> Any:
    """один синхронний запит, повертає json."""
    resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _retry_after(headers: Any, default: float) -> float:
    try:
        return min(float(headers.get("Retry-After", default)), MAX_WAIT)
    except (TypeError, ValueError):
        return default


async def get_json(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, Any],
    *,
    timeout: float = 10,
    attempts: int = ATTEMPTS,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> Any:
    """json відповіді або None, якщо об'єкта немає (4xx, порожня відповідь).

    429, 5xx, таймаут, обрив і битий json повторює до attempts разів,
    потім кидає Failed.
    """
    for attempt in range(attempts):
        wait = 2.0**attempt
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return json.loads(text) if text.strip() else None
                elif resp.status == 429 or resp.status >= 500:
                    wait = _retry_after(resp.headers, wait)
                else:
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            pass  # ValueError: битий json або кодування
        if attempt + 1 < attempts:
            await sleep(wait)
    raise Failed


def session(workers: int) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=workers), headers=HEADERS
    )


# сканування

class Progress:
    """один рядок, що оновлюється на місці через \\r."""

    def __init__(self, total: int, unit: str, every: float = 0.2) -> None:
        self.total = max(total, 1)
        self.unit = unit
        self.every = every
        self._last = 0.0
        self._width = 0

    def show(self, done: int, extra: str = "", force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < self.every:
            return
        self._last = now
        pct = done * 100 // self.total
        line = f"{pct:3d}%  {done}/{self.total} {self.unit}{extra}"
        pad = " " * max(0, self._width - len(line))
        self._width = max(self._width, len(line))
        sys.stdout.write("\r" + line + pad)
        sys.stdout.flush()

    def finish(self) -> None:
        sys.stdout.write("\n")
        sys.stdout.flush()


@dataclass
class Scan:
    found: list[Any] = field(default_factory=list)
    failed: list[Any] = field(default_factory=list)
    checked: int = 0
    done: int = 0


async def scan(
    keys: Iterable[Any],
    total: int,
    fetch: Callable[[Any], Awaitable[Any]],
    handle: Callable[[Any], tuple[int, list[Any]]],
    *,
    workers: int = 15,
    unit: str = "",
    found_label: str | None = "знайдено",
    result: Scan | None = None,
) -> Scan:
    """проходить ключі пулом воркерів.

    fetch(ключ) дає json, None або кидає Failed; handle(json) повертає
    (скільки товарів переглянуто, знахідки).
    """
    res = result or Scan()
    keys = iter(keys)
    progress = Progress(total, unit)

    def extra() -> str:
        text = f"  {found_label} {len(res.found)}" if found_label else f"  товарів {res.checked}"
        return text + (f"  збоїв {len(res.failed)}" if res.failed else "")

    async def worker() -> None:
        for key in keys:
            try:
                data = await fetch(key)
            except Failed:
                res.failed.append(key)
            else:
                if data is not None:
                    checked, found = handle(data)
                    res.checked += checked
                    res.found.extend(found)
            res.done += 1
            progress.show(res.done, extra())

    await asyncio.gather(*(worker() for _ in range(workers)))
    progress.show(res.done, extra(), force=True)
    progress.finish()
    return res


def failed_line(what: str, failed: Sequence[Any], limit: int = 20) -> str:
    """короткий рядок про те, що не вдалося отримати."""
    ids = ", ".join(str(x) for x in sorted(failed)[:limit])
    more = ", …" if len(failed) > limit else ""
    return f"без відповіді після {ATTEMPTS} спроб, {what}: {len(failed)} ({ids}{more})"


# таблиця

def table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """колонки вирівняні; числові праворуч, остання (назва) обрізається під ширину."""
    rows = [list(r) for r in rows]
    fixed = len(header) - 1
    widths = [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(fixed)]
    room = shutil.get_terminal_size((100, 20)).columns - sum(widths) - 2 * fixed - 1
    room = max(room, 20)

    def line(cells: Sequence[str]) -> str:
        left = "  ".join(c.rjust(w) for c, w in zip(cells, widths))
        name = cells[-1]
        if len(name) > room:
            name = name[: room - 1] + "…"
        return f"{left}  {name}"

    return "\n".join([dim(line(header)), *(line(r) for r in rows)])
