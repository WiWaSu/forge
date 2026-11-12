from __future__ import annotations

import math
from typing import Any, Optional

from ..data import loader
from ..models import ForgingResult, Part, Primitive, Quantity
from . import geometry

SRC = "ГОСТ 7829-70"
TABLE_FILE = "gost7829_tables.json"

KIND_NAMES = {
    "shaft": "круглого сечения, гладкая или с уступами",
    "rect_long": "прямоугольного сечения, удлинённая",
    "disc": "диск или цилиндр",
    "disc_hole": "диск, цилиндр или втулка с отверстием",
    "bar_plate": "брусок или пластина",
    "ring": "кольцо раскатное",
    "bored_cylinder": "цилиндр с отверстием",
    "hollow_shaft": "вал полый",
    "stepped_bushing": "втулка с уступами (фланец и ступица), в подкладных кольцах",
}
KIND_TABLES = {
    "shaft": "табл. 2 и 3", "rect_long": "табл. 2", "disc": "табл. 7", "disc_hole": "табл. 7",
    "bar_plate": "табл. 7", "ring": "табл. 8", "bored_cylinder": "табл. 9",
    "hollow_shaft": "табл. 10 и 11", "stepped_bushing": "табл. 12 и 3",
}


class Unsupported(Exception):
    pass


def tables() -> dict[str, Any]:
    return loader.load(TABLE_FILE)


def _in(rng: list, x: float) -> bool:
    lo, hi = rng
    return x > lo and (hi is None or x <= hi)


def _index(ranges: list, x: float) -> Optional[int]:
    for i, r in enumerate(ranges):
        if _in(r, x):
            return i
    return None


def _range_text(rng: list) -> str:
    lo, hi = rng
    if not lo:
        return f"до {hi}"
    return f"св. {lo}" if hi is None else f"св. {lo} до {hi}"


def _pick(values: list, col: Optional[int], what: str) -> tuple[float, float]:
    if col is None or col >= len(values) or values[col] is None:
        raise Unsupported(f"{what}: сочетание размеров вне таблицы")
    allowance, half_tol = values[col]
    return float(allowance), float(half_tol)


def table2(length: float, size: float) -> tuple[float, float, str]:
    t = tables()["table2"]
    row = next((r for r in t["rows"] if _in(r["length"], length)), None)
    if row is None:
        raise Unsupported(f"длина {length:.0f} мм вне таблицы 2 (до 6000 мм)")
    col = _index(t["columns"], size)
    a, h = _pick(row["values"], col, f"табл. 2, L {_range_text(row['length'])}, размер {size:.0f} мм")
    return a, h, f"{SRC}, табл. 2: L {_range_text(row['length'])}, D {_range_text(t['columns'][col])}"


def table3(diff: float) -> float:
    t = tables()["table3"]
    i = _index(t["ranges"], diff)
    return float(t["values"][i if i is not None else 0])


def table4(diameter: float) -> float:
    t = tables()["table4"]
    i = _index(t["ranges"], diameter)
    return float(t["values"][i if i is not None else -1])


def table7(size: float, height: float, key: str) -> tuple[float, float, str]:
    t = tables()["table7"]
    row = next((r for r in t["rows"] if _in(r["size"], size)), None)
    if row is None:
        raise Unsupported(f"размер {size:.0f} мм вне таблицы 7 (до 700 мм)")
    col = _index(t["columns"], height)
    a, h = _pick(row[key], col, f"табл. 7, D {_range_text(row['size'])}, H {height:.0f} мм")
    return a, h, f"{SRC}, табл. 7: D {_range_text(row['size'])}, H {_range_text(t['columns'][col])}"


def table8(diameter: float, height: float, key: str, wall: float = 0.0) -> tuple[float, float, str]:
    t = tables()["table8"]
    row = next((r for r in t["rows"] if _in(r["size"], diameter)), None)
    if row is None:
        raise Unsupported(f"диаметр {diameter:.0f} мм вне таблицы 8 (до 1200 мм)")
    col = _index(t["columns"], height)
    where = f"{SRC}, табл. 8: D {_range_text(row['size'])}, H {_range_text(t['columns'][col])}" \
        if col is not None else ""
    if key != "d":
        a, h = _pick(row[key], col, f"табл. 8, D {_range_text(row['size'])}, H {height:.0f} мм")
        return a, h, where
    lines = row["d"]
    chosen = next((ln for ln in lines if _in(ln["wall"], wall)), None)
    if chosen is None:
        chosen = next((ln for ln in lines if ln["wall"][0] >= wall), lines[-1])
    a, h = _pick(chosen["values"], col, f"табл. 8, D {_range_text(row['size'])}, D−d {wall:.0f} мм")
    return a, h, f"{where}, D − d {_range_text(chosen['wall'])}"


def table9(height: float, diameter: float) -> tuple[dict, str]:
    t = tables()["table9"]
    rows = [r for r in t["rows"] if _in(r["height"], height)]
    if not rows:
        raise Unsupported(f"высота {height:.0f} мм вне таблицы 9")
    row = next((r for r in rows if _in(r["diameter"], diameter)), None)
    if row is None:
        raise Unsupported(f"табл. 9: при высоте {height:.0f} мм диаметра {diameter:.0f} мм нет")
    return row, (f"{SRC}, табл. 9: H {_range_text(row['height'])}, "
                 f"D {_range_text(row['diameter'])}")


def _asym(value: list) -> tuple[float, float, float]:
    if len(value) == 2:
        return float(value[0]), float(value[1]), -float(value[1])
    return float(value[0]), float(value[1]), float(value[2])


def table12(size: float, height: float, key: str, table: str = "table12") -> tuple[float, float, float, str]:
    t = tables()[table]
    no = table[-2:]
    row = next((r for r in t["rows"] if _in(r["size"], size)), None)
    if row is None or key not in row:
        raise Unsupported(f"табл. {no}: размер {size:.0f} мм вне таблицы")
    col = _index(t["columns"], height)
    if col is None or row[key][col] is None:
        raise Unsupported(f"табл. {no}: при диаметре {size:.0f} мм высоты {height:.0f} мм нет")
    a, up, low = _asym(row[key][col])
    return a, up, low, f"{SRC}, табл. {no}: D {_range_text(row['size'])}, H {_range_text(t['columns'][col])}"


def table10(length: float, diameter: float) -> tuple[float, float, str]:
    t = tables()["table10"]
    row = next((r for r in t["rows"] if _in(r["length"], length)), None)
    if row is None:
        raise Unsupported(f"длина {length:.0f} мм вне таблицы 10")
    col = _index(t["columns"], diameter)
    a, h = _pick(row["values"], col, f"табл. 10, L {length:.0f}, D {diameter:.0f} мм")
    return a, h, f"{SRC}, табл. 10: L {_range_text(row['length'])}, D {_range_text(t['columns'][col])}"


def table11(hole: float) -> tuple[float, float, str]:
    t = tables()["table11"]
    i = _index(t["ranges"], hole)
    if i is None:
        raise Unsupported(f"отверстие {hole:.0f} мм вне таблицы 11 (120–280 мм)")
    d_forge, half = t["hole"][i]
    return float(d_forge), float(half), f"{SRC}, табл. 11: d {_range_text(t['ranges'][i])}"


def _hole_diameter(prims: list[Primitive]) -> float:
    d = 0.0
    for p in prims:
        if p.subtract and p.kind == "cylinder":
            d = max(d, p.d)
        if not p.subtract and p.kind == "tube":
            d = max(d, p.d_inner)
    return d


def classify(part: Part) -> tuple[str, dict[str, float]]:
    solid = [p for p in part.primitives if not p.subtract]
    boxes = [p for p in solid if p.kind == "box"]
    if boxes:
        if len(solid) != 1:
            raise Unsupported("деталь из параллелепипеда с другими элементами (рычаг, кронштейн) "
                              "в таблице 1 ГОСТ 7829-70 не предусмотрена")
        h, b, l = sorted((boxes[0].a, boxes[0].b, boxes[0].c))
        dims = {"H": h, "B": b, "L": l}
        if l > 1.5 * b and b <= 1.5 * h:
            return "rect_long", dims
        if b <= l <= 1.5 * b:
            return "bar_plate", dims
        raise Unsupported(f"полоса {l:.0f}×{b:.0f}×{h:.0f} мм: длина больше полутора ширин, "
                          f"а ширина больше полутора толщин — такого типа в таблице 1 нет")
    if any(not p.along_axis for p in solid):
        raise Unsupported("элемент поперёк оси (бобышка) — форма вне таблицы 1 ГОСТ 7829-70")

    dims_overall = geometry.overall(solid)
    D, L = dims_overall["max_cross_mm"], dims_overall["length_mm"]
    d = _hole_diameter(part.primitives)
    dims = {"D": D, "L": L, "d": d}
    if d > 0.5 * D:
        if L <= D:
            return "ring", dims
        if L <= 1.5 * D:
            return "bored_cylinder", dims
        return "hollow_shaft", dims
    if L > 1.5 * D:
        return "shaft", dims
    bushing = stepped_bushing_dims(solid, d)
    if bushing:
        return "stepped_bushing", {**dims, **bushing}
    return ("disc_hole" if d > 0 else "disc"), dims


def stepped_bushing_dims(solid: list[Primitive], d: float) -> Optional[dict[str, float]]:
    if len(solid) != 2 or any(p.kind != "cylinder" for p in solid):
        return None
    flange, hub = sorted(solid, key=lambda p: p.d, reverse=True)
    D1, h, D2 = flange.d, flange.l, hub.d
    H = flange.l + hub.l
    ok = h <= D1 and h < 0.75 * H and D1 - D2 >= 0.2 * D1
    if d:
        ok = ok and 0.5 * D2 >= d >= 0.4 * H
    return {"D1": D1, "h": h, "D2": D2, "H": H} if ok else None


def length_tolerance(half_tol: float, factor: float | None = None) -> float:
    k = factor if factor is not None else tables()["rules"]["stepped_length_factor"]
    return float(math.ceil(k * half_tol - 1e-9))


def _round_up(x: float) -> float:
    return float(math.ceil(x - 1e-9))


def _of(word: str, name: str) -> str:
    from .forging import genitive
    return f"{word} {genitive(name)}" if name else word


def _row(label: str, element: str, part_mm: float, forging_mm: float, half: Any,
         source: str) -> dict[str, Any]:
    upper, lower = (half if isinstance(half, tuple) else (half, -half))
    tol = Quantity(upper - lower, "мм", source, True, "поле предельных отклонений Δ")
    return {
        "element": element,
        "parameter": label,
        "label": label,
        "part_mm": round(part_mm, 2),
        "allowance_mm": round(abs(forging_mm - part_mm) / 2.0, 2),
        "forging_mm": round(forging_mm, 1),
        "tolerance": tol.to_dict(),
        "upper": Quantity(upper, "мм", source, True, "верхнее отклонение").to_dict(),
        "lower": Quantity(lower, "мм", source, True, "нижнее отклонение").to_dict(),
    }


def extra_allowances(diams: list[float], lengths: list[float]) -> tuple[list[float], int]:
    imax = max(range(len(diams)), key=lambda i: diams[i])
    extra = [table3(diams[imax] - d) if i != imax else 0.0 for i, d in enumerate(diams)]
    base_area = diams[imax] * lengths[imax]
    candidates = {i: extra[i] * (diams[i] * lengths[i] - base_area)
                  for i in range(len(diams)) if i != imax and diams[i] * lengths[i] > base_area}
    main_i = max(candidates, key=candidates.get) if candidates else imax
    if main_i != imax:
        extra[imax], extra[main_i] = extra[main_i], 0.0
    return extra, main_i


def _shaft(part: Part, L: float, res: ForgingResult) -> list[Primitive]:
    steps = [p for p in part.primitives if not p.subtract]
    k = tables()["rules"]["stepped_length_factor"]

    main = [table2(L, p.d) for p in steps]
    forged_d = [p.d + a for p, (a, _, _) in zip(steps, main)]  # диаметры поковки с основным припуском, мм
    imax = max(range(len(steps)), key=lambda i: forged_d[i])

    extra, main_i = extra_allowances(forged_d, [p.l for p in steps])
    if main_i != imax:
        res.warnings.append(
            f"Основное сечение по п. 15 ГОСТ 7829-70 — ⌀{steps[main_i].d:.0f}, а не наибольший "
            f"диаметр: его дополнительный припуск перенесён на ⌀{steps[imax].d:.0f}")
    diam = [_round_up(fd + s) for fd, s in zip(forged_d, extra)]  # с дополнительным припуском, мм

    merged = True
    while merged and len(diam) > 1:
        merged = False
        for i in range(len(diam) - 1):
            small, big = sorted((diam[i], diam[i + 1]))
            if small != big and 0.5 * (big - small) < table4(small):
                j = i if diam[i] < diam[i + 1] else i + 1
                res.warnings.append(
                    f"Уступ ⌀{diam[j]:.0f} высотой {0.5 * (big - small):.1f} мм ниже выполнимого "
                    f"по табл. 4 ГОСТ 7829-70 ({table4(small):.0f} мм) — откован по соседнему "
                    f"диаметру ⌀{big:.0f}")
                diam[j] = big
                merged = True

    a_max, h_max, src_max = main[imax]
    add_len = k * a_max  # припуск на длину вала, мм
    lengths = [p.l for p in steps]
    if len(lengths) == 1:
        lengths[0] += add_len
    else:
        lengths[0] += add_len / 2
        lengths[-1] += add_len / 2
    if len(steps) > 1:
        res.warnings.append(
            "Припуск на длину уступов по черт. 3 ГОСТ 7829-70 кратен припуску наибольшего сечения "
            "и зависит от базы на чертеже поковки; здесь общий припуск 2,5δ положен поровну "
            "на крайние уступы")

    prims = []
    for p, d_new, l_new, (a, h, src), s in zip(steps, diam, lengths, main, extra):
        prims.append(Primitive(kind=p.kind, d=d_new, d_inner=p.d_inner, l=_round_up(l_new),
                               name=p.name, along_axis=p.along_axis))
        note = f"{src}; доп. припуск S = {s:.0f} мм (табл. 3)" if s else src
        res.dimensions.append(_row(_of("диаметр", p.name),
                                   p.name or p.kind, p.d, d_new, h, note))
    total_part = sum(p.l for p in steps)
    res.dimensions.append(_row("общая длина", "поковка", total_part, sum(q.l for q in prims),
                               length_tolerance(h_max), f"{SRC}, п. 15: 2,5 припуска и отклонения "
                               f"наибольшего сечения ({src_max})"))
    if _hole_diameter(part.primitives):
        res.warnings.append("Отверстие в вале меньше половины диаметра: по табл. 1 ГОСТ 7829-70 "
                            "полым валом поковка не считается, отверстие не прошивается")
    return prims


def _hole(d_part: float, half_and_allow: tuple[float, float, str], h_forge: float,
          res: ForgingResult, label: str = "внутренний диаметр") -> Optional[Primitive]:
    rules = tables()["rules"]
    a, h, src = half_and_allow
    d_forge = d_part - a  # диаметр отверстия в поковке, мм
    if d_part <= rules["hole_not_pierced_max_d"] or d_forge <= 0:
        res.warnings.append(
            f"Отверстие ⌀{d_part:.0f} не прошивается: {rules['hole_note']}")
        return None
    if h_forge / d_forge > rules["hole_not_pierced_ratio"]:
        res.warnings.append(
            f"Отверстие ⌀{d_part:.0f} не прошивается: высота поковки к диаметру прошиваемого "
            f"отверстия {h_forge / d_forge:.1f} > {rules['hole_not_pierced_ratio']:g} "
            f"(ГОСТ 7829-70, табл. 7, прим. 2)")
        return None
    res.dimensions.append(_row(label, "отверстие", d_part, d_forge, h, src))
    return Primitive(kind="cylinder", d=d_forge, l=h_forge, subtract=True, name="отверстие")


def _disc(part: Part, dims: dict, res: ForgingResult, with_hole: bool) -> list[Primitive]:
    D, H = dims["D"], dims["L"]
    solid = [p for p in part.primitives if not p.subtract]
    if len(solid) > 1:
        res.warnings.append(
            "Ступенчатый диск не подходит под условия таблицы 1 ГОСТ 7829-70 для втулки "
            "с уступами (фланец и одна ступица, h < 0,75H, D1 − D2 ≥ 0,2D1). Поковка назначена "
            "гладким диском по габариту, уступы оформляются напуском и снимаются при обработке")
    aD, hD, sD = table7(D, H, "D")
    aH, hH, sH = table7(D, H, "H")
    D2, H2 = _round_up(D + aD), _round_up(H + aH)
    res.dimensions.append(_row("наружный диаметр", "поковка", D, D2, hD, sD))
    res.dimensions.append(_row("высота", "поковка", H, H2, hH, sH))
    prims = [Primitive(kind="cylinder", d=D2, l=H2, name="поковка")]
    if with_hole:
        hole = _hole(dims["d"], table7(D, H, "d"), H2, res)
        if hole:
            prims.append(hole)
    return prims


def bushing_extra_on_hub(D1f: float, hf: float, D2f: float, Hf: float) -> bool:
    return D1f * hf > D2f * (Hf - hf)


def _stepped_bushing(part: Part, dims: dict, res: ForgingResult) -> list[Primitive]:
    D1, h, D2, H, d = dims["D1"], dims["h"], dims["D2"], dims["H"], dims["d"]
    if D2 > 360:
        res.warnings.append("Ступица диаметром более 360 мм по прим. 4 к табл. 12 ГОСТ 7829-70 "
                            "не выполняется — поковка назначена гладким диском")
        return _disc(part, dims, res, with_hole=bool(d))
    aH, uH, lH, sH = table12(D1, H, "H")
    aD1, uD1, lD1, sD1 = table12(D1, H, "D1")
    aD2, uD2, lD2, sD2 = table12(D2, H, "D2")
    if 80 < D2 <= 110:
        res.warnings.append("Отклонения на ступицу ⌀80…110 в перепечатке табл. 12 набраны "
                            "симметричными, в соседних строках — несимметричные; требует сверки "
                            "по оригиналу")
    Hf, hf = _round_up(H + aH), _round_up(h + aH)
    D1f, D2f = D1 + aD1, D2 + aD2
    extra = table3(D1f - D2f)
    on_hub = bushing_extra_on_hub(D1f, hf, D2f, Hf)
    if on_hub:
        D2f += extra
    else:
        D1f += extra
    D1f, D2f = _round_up(D1f), _round_up(D2f)
    res.warnings.append(
        f"Доп. припуск на несоосность {extra:.0f} мм (табл. 3, D1′ − D2′) назначен на "
        f"{'ступицу' if on_hub else 'фланец'} по п. 21в ГОСТ 7829-70")

    flange_src = next(p for p in part.primitives if not p.subtract and p.d == D1)
    hub_src = next(p for p in part.primitives if not p.subtract and p is not flange_src)
    res.dimensions.append(_row(_of("диаметр", flange_src.name or "фланец"), "фланец",
                               D1, D1f, (uD1, lD1), sD1))
    res.dimensions.append(_row(_of("высота", flange_src.name or "фланец"), "фланец",
                               h, hf, (uH, lH), sH))
    res.dimensions.append(_row(_of("диаметр", hub_src.name or "ступица"), "ступица",
                               D2, D2f, (uD2, lD2),
                               f"{sD2}; доп. припуск {extra:.0f} мм (табл. 3)" if on_hub else sD2))
    res.dimensions.append(_row("общая высота", "поковка", H, Hf, (uH, lH), sH))

    forged = {id(flange_src): Primitive(kind="cylinder", d=D1f, l=hf, name=flange_src.name),
              id(hub_src): Primitive(kind="cylinder", d=D2f, l=Hf - hf, name=hub_src.name)}
    prims = [forged[id(p)] for p in part.primitives if not p.subtract]
    if d:
        ad, ud, ld, sd = table12(D1, H, "d")
        d_forge = d - ad
        rules = tables()["rules"]
        if d < rules["hole_not_pierced_max_d"] or d_forge <= 0 or Hf / d_forge > 2.5:
            res.warnings.append(
                f"Отверстие ⌀{d:.0f} не прошивается: прим. 2 к табл. 12 ГОСТ 7829-70 разрешает "
                f"не прошивать отверстие менее 40 мм и при H′/d′ более 2,5 "
                f"(здесь {Hf / max(d_forge, 1):.1f})")
        else:
            res.dimensions.append(_row("внутренний диаметр", "отверстие", d, d_forge, (ud, ld), sd))
            prims.append(Primitive(kind="cylinder", d=d_forge, l=Hf, subtract=True, name="отверстие"))
    return prims


def _ring(part: Part, dims: dict, res: ForgingResult) -> list[Primitive]:
    D, H, d = dims["D"], dims["L"], dims["d"]
    aD, hD, sD = table8(D, H, "D")
    aH, hH, sH = table8(D, H, "H")
    D2, H2 = _round_up(D + aD), _round_up(H + aH)
    res.dimensions.append(_row("наружный диаметр", "кольцо", D, D2, hD, sD))
    res.dimensions.append(_row("высота", "кольцо", H, H2, hH, sH))
    ad, hd, sd = table8(D, H, "d", wall=D - d)
    d2 = d - ad
    res.dimensions.append(_row("внутренний диаметр", "кольцо", d, d2, hd, sd))
    return [Primitive(kind="tube", d=D2, d_inner=d2, l=H2, name="кольцо")]


def _bored_cylinder(part: Part, dims: dict, res: ForgingResult) -> list[Primitive]:
    D, H, d = dims["D"], dims["L"], dims["d"]
    row, src = table9(H, D)
    D2, H2 = _round_up(D + row["D"][0]), _round_up(H + row["H"][0])
    res.dimensions.append(_row("наружный диаметр", "цилиндр", D, D2, row["D"][1], src))
    res.dimensions.append(_row("высота", "цилиндр", H, H2, row["H"][1], src))
    prims = [Primitive(kind="cylinder", d=D2, l=H2, name="цилиндр")]
    hole = _hole(d, (row["d"][0], row["d"][1], src), H2, res)
    if hole:
        prims.append(hole)
    return prims


def _hollow_shaft(part: Part, dims: dict, res: ForgingResult) -> list[Primitive]:
    D, L, d = dims["D"], dims["L"], dims["d"]
    aD, hD, sD = table10(L, D)
    rule = tables()["table10"]["length_rule"]
    factor = rule["long"]["factor"] if L >= 2 * D else rule["short"]["factor"]
    D2, L2 = _round_up(D + aD), _round_up(L + factor * aD)
    d2, hd, sd = table11(d)
    res.dimensions.append(_row("наружный диаметр", "вал", D, D2, hD, sD))
    res.dimensions.append(_row("длина", "вал", L, L2, length_tolerance(hD, factor),
                               f"{SRC}, п. 20г: {factor} припуска на наибольший диаметр"))
    res.dimensions.append(_row("внутренний диаметр", "вал", d, d2, hd, sd))
    return [Primitive(kind="tube", d=D2, d_inner=d2, l=L2, name="вал")]


def _box(part: Part, kind: str, dims: dict, res: ForgingResult) -> list[Primitive]:
    H, B, L = dims["H"], dims["B"], dims["L"]
    box = part.primitives[0]
    if kind == "rect_long":
        a, h, src = table2(L, B)
        k = tables()["rules"]["stepped_length_factor"]
        new = (_round_up(H + a), _round_up(B + a), _round_up(L + k * a))
        rows = (("толщина", H, new[0], h, src), ("ширина", B, new[1], h, src),
                ("длина", L, new[2], length_tolerance(h), f"{SRC}, черт. 2: 2,5 припуска сечения"))
    else:
        aH, hH, sH = table7(L, H, "H")
        aB, hB, sB = table7(L, H, "D")
        new = (_round_up(H + aH), _round_up(B + aB), _round_up(L + aB))
        rows = (("толщина", H, new[0], hH, sH), ("ширина", B, new[1], hB, sB),
                ("длина", L, new[2], hB, sB))
    for label, was, now, half, src in rows:
        res.dimensions.append(_row(label, box.name or "брусок", was, now, half, src))
    return [Primitive(kind="box", a=new[2], b=new[1], c=new[0], name=box.name)]


def calculate(part: Part, res: ForgingResult) -> ForgingResult:
    kind, dims = classify(part)
    res.norm = SRC
    res.forging_kind = f"{KIND_NAMES[kind]} ({SRC}, табл. 1; {KIND_TABLES[kind]})"
    res.dimensions = []

    if not part.machined_all_over:
        res.warnings.append(
            "Деталь обрабатывается не полностью: по п. 7 ГОСТ 7829-70 на необрабатываемые "
            "поверхности припуск не назначается, но какие поверхности не обрабатываются, "
            "в форме не указано — припуск назначен на все")

    if kind == "shaft":
        prims = _shaft(part, dims["L"], res)
    elif kind == "stepped_bushing":
        prims = _stepped_bushing(part, dims, res)
    elif kind in ("disc", "disc_hole"):
        prims = _disc(part, dims, res, with_hole=(kind == "disc_hole"))
    elif kind == "ring":
        prims = _ring(part, dims, res)
    elif kind == "bored_cylinder":
        prims = _bored_cylinder(part, dims, res)
    elif kind == "hollow_shaft":
        prims = _hollow_shaft(part, dims, res)
    else:
        prims = _box(part, kind, dims, res)

    res.primitives = prims
    rho = loader.density()
    mass = geometry.mass(prims, rho)
    res.mass_actual = Quantity(mass, "кг", f"{SRC}, п. 12: по номинальным размерам поковки",
                               True, "M = V * плотность")
    if not loader.is_verified(TABLE_FILE):
        res.warnings.append(
            "Припуски по ГОСТ 7829-70 перенесены с перепечаток стандарта, сверенных между собой "
            "и по примерам приложения; сверка по оригиналу не выполнена")
    return res
