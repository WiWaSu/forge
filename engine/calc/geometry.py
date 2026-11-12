from __future__ import annotations

import math
from typing import Iterable

from ..models import Primitive

MM3_IN_M3 = 1_000_000_000.0


def primitive_volume(p: Primitive) -> float:
    if p.kind == "cylinder":
        v = math.pi * p.d ** 2 / 4.0 * p.l  # объём цилиндра, мм3
    elif p.kind == "tube":
        v = math.pi * (p.d ** 2 - p.d_inner ** 2) / 4.0 * p.l  # объём трубы, мм3
    elif p.kind == "box":
        v = p.a * p.b * p.c  # объём параллелепипеда, мм3
    else:
        raise ValueError(f"Неизвестный тип примитива: {p.kind}")
    return -v if p.subtract else v


def volume(primitives: Iterable[Primitive]) -> float:
    return sum(primitive_volume(p) for p in primitives)


def mass(primitives: Iterable[Primitive], density_kg_m3: float) -> float:
    return volume(primitives) / MM3_IN_M3 * density_kg_m3  # масса M = V * плотность, кг


def overall(primitives: Iterable[Primitive]) -> dict[str, float]:
    max_cross = 0.0
    length = 0.0
    for p in primitives:
        if p.subtract:
            continue
        if p.kind in ("cylinder", "tube"):
            max_cross = max(max_cross, p.d)
            if p.along_axis:
                length += p.l
        elif p.kind == "box":
            a, b, c = sorted((p.a, p.b, p.c))
            max_cross = max(max_cross, math.hypot(a, b))  # диагональ сечения, мм
            if p.along_axis:
                length += c
    return {"max_cross_mm": max_cross, "length_mm": length}


def box_extents(primitives: Iterable[Primitive]) -> tuple[float, float, float]:
    solids = [p for p in primitives if not p.subtract]
    length = overall(solids)["length_mm"]
    width = height = 0.0
    for p in solids:
        if p.kind in ("cylinder", "tube"):
            width = max(width, p.d)
            height = max(height, p.d if p.along_axis else p.l)
            if not p.along_axis:
                length = max(length, p.d)
        elif p.kind == "box":
            small, middle, large = sorted((p.a, p.b, p.c))
            width = max(width, middle)
            height = max(height, small)
            if not p.along_axis:
                length = max(length, large)
    return length, width, height


def envelope_volume(primitives: Iterable[Primitive]) -> float:
    prims = list(primitives)
    dims = overall(prims)
    cylinder = math.pi * dims["max_cross_mm"] ** 2 / 4.0 * dims["length_mm"]  # объём описанного цилиндра, мм3
    length, width, height = box_extents(prims)
    figures = [v for v in (cylinder, length * width * height) if v > 0]
    return min(figures, default=0.0)


def plan(primitives: Iterable[Primitive], upright: bool) -> tuple[float, float]:
    prims = [p for p in primitives if not p.subtract]
    if not prims:
        return 0.0, 0.0
    dims = overall(prims)
    d_max, length = dims["max_cross_mm"], dims["length_mm"]
    if any(p.kind == "box" for p in prims):
        a = max(p.a for p in prims if p.kind == "box")
        b = max(p.b for p in prims if p.kind == "box")
        return a * b, 2 * (a + b)  # площадь и периметр прямоугольника в плане, мм2 и мм
    if upright or length <= d_max:
        return math.pi * d_max ** 2 / 4.0, math.pi * d_max  # площадь и периметр круга в плане, мм2 и мм
    steps = [p.d for p in prims if p.along_axis]
    area = sum(p.d * p.l for p in prims if p.along_axis)  # площадь ступенчатого контура в плане, мм2
    perimeter = 2 * length + steps[0] + steps[-1] + sum(abs(x - y) for x, y in zip(steps, steps[1:]))  # периметр контура в плане, мм
    return area, perimeter


def scale_primitives(primitives: Iterable[Primitive], allowance_fn) -> list[Primitive]:
    result: list[Primitive] = []
    for p in primitives:
        q = Primitive(**{**p.to_dict()})
        if p.kind in ("cylinder", "tube"):
            z_d = allowance_fn(p.d, p, "d")
            z_l = allowance_fn(p.l, p, "l")
            if p.subtract:
                q.d = max(p.d - 2 * z_d, 0.0)
            else:
                q.d = p.d + 2 * z_d
                if p.d_inner:
                    z_in = allowance_fn(p.d_inner, p, "d_inner")
                    q.d_inner = max(p.d_inner - 2 * z_in, 0.0)
            q.l = p.l + 2 * z_l
        elif p.kind == "box":
            q.a = p.a + 2 * allowance_fn(p.a, p, "a")
            q.b = p.b + 2 * allowance_fn(p.b, p, "b")
            q.c = p.c + 2 * allowance_fn(p.c, p, "c")
        result.append(q)
    return result
