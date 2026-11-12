from __future__ import annotations

import re
from typing import Any, Iterable

NUMBER_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?![\w])")

COUNTING_MAX = 12

REL_TOLERANCE = 0.005
ABS_TOLERANCE = 0.06


def extract(text: str) -> list[float]:
    return [float(m.replace(",", ".")) for m in NUMBER_RE.findall(text)]


def collect_allowed(data: Any, acc: set[float] | None = None) -> set[float]:
    if acc is None:
        acc = set()
    if isinstance(data, bool):
        return acc
    if isinstance(data, (int, float)):
        value = float(data)
        acc.add(value)
        acc.add(round(value))
        acc.add(round(value, 1))
        acc.add(round(value, 2))
        return acc
    if isinstance(data, dict):
        for v in data.values():
            collect_allowed(v, acc)
    elif isinstance(data, (list, tuple)):
        for v in data:
            collect_allowed(v, acc)
    return acc


def is_allowed(value: float, allowed: Iterable[float]) -> bool:
    if value == int(value) and 0 <= value <= COUNTING_MAX:
        return True
    for a in allowed:
        if abs(value - a) <= max(ABS_TOLERANCE, abs(a) * REL_TOLERANCE):
            return True
    return False


def check(text: str, allowed: Iterable[float]) -> list[float]:
    allowed = set(allowed)
    return [v for v in extract(text) if not is_allowed(v, allowed)]


def format_error(invented: list[float], where: str = "") -> str:
    listed = ", ".join(f"{v:g}" for v in invented)
    prefix = f"{where}: " if where else ""
    return (
        f"{prefix}числа {listed} отсутствуют в расчёте. "
        f"Любое число в переходе должно быть взято из блока РАСЧЁТ. "
        f"Убери их или замени на посчитанные значения"
    )
