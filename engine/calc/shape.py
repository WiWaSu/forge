from __future__ import annotations

from typing import Iterable

from ..models import Primitive
from . import geometry

TYPE_NAMES = {
    "shaft": "вал",
    "gear": "шестерня",
    "flange": "фланец",
    "ring": "кольцо",
    "lever": "рычаг",
}


def _fmt(x: float) -> str:
    return f"{x:.0f}"


def problems(part_type: str, primitives: Iterable[Primitive]) -> list[str]:
    prims = list(primitives)
    solid = [p for p in prims if not p.subtract]
    if not solid or part_type not in TYPE_NAMES:
        return []

    dims = geometry.overall(solid)
    length, cross = dims["length_mm"], dims["max_cross_mm"]
    boxes = [p for p in solid if p.kind == "box"]
    across = [p for p in solid if not p.along_axis]
    has_hole = any(p.subtract for p in prims) or any(
        p.kind == "tube" and p.d_inner > 0 for p in solid)
    out: list[str] = []

    if part_type == "shaft":
        if boxes:
            out.append("у вала все элементы — тела вращения (цилиндр, труба); "
                       "параллелепипед бывает у рычага или плиты")
        if across:
            out.append("у вала все ступени лежат на одной оси; элемент поперёк оси "
                       "(бобышка) — признак рычага или комбинированной детали")
        if not boxes and length <= cross:
            out.append(f"вал — удлинённая деталь, а здесь длина {_fmt(length)} мм "
                       f"не больше диаметра {_fmt(cross)} мм; такая деталь круглая "
                       f"в плане — тип «шестерня», «фланец» или «кольцо»")

    elif part_type in ("gear", "ring"):
        name = TYPE_NAMES[part_type]
        if boxes:
            out.append(f"{name} — тело вращения, а в геометрии есть параллелепипед")
        if across:
            out.append(f"у детали типа «{name}» все элементы соосны; элемент поперёк оси "
                       f"бывает у рычага или комбинированной детали")
        if not boxes and length > cross:
            out.append(f"{name} — деталь круглая в плане, а здесь высота {_fmt(length)} мм "
                       f"больше диаметра {_fmt(cross)} мм; удлинённая деталь — тип «вал»")
        if part_type == "ring" and not has_hole:
            out.append("у кольца нет отверстия: задайте трубу или вычитаемый цилиндр, "
                       "иначе это диск — тип «шестерня»")

    elif part_type == "flange":
        if across:
            out.append("у фланца все элементы соосны; элемент поперёк оси бывает "
                       "у рычага или комбинированной детали")
        if not boxes and length > cross:
            out.append(f"фланец — деталь плоская, а здесь высота {_fmt(length)} мм "
                       f"больше диаметра {_fmt(cross)} мм; удлинённая деталь — тип «вал»")

    elif part_type == "lever":
        if not boxes:
            out.append("тело рычага задаётся параллелепипедом, а здесь только тела "
                       "вращения — это вал, шестерня или кольцо")
        else:
            body = max(boxes, key=lambda p: p.a * p.b * p.c)
            small, mid, big = sorted((body.a, body.b, body.c))
            if big <= mid:
                out.append(f"рычаг — удлинённая деталь, а у его тела длина {_fmt(big)} мм "
                           f"не больше ширины {_fmt(mid)} мм; квадратная плита — тип «фланец»")
    return out
