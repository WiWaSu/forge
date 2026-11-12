from __future__ import annotations

import math

from ..data import loader
from .. import names
from ..models import BlankResult, ForgingResult, MaterialItem, Part, Quantity
from . import geometry
from .forging import stack

SRC_LOSSES = "knowledge/reference/losses.json"

UPSET_RATIO_MIN = 1.25
UPSET_RATIO_MAX = 2.5
UPSET_RATIO_TARGET = 1.8

UPSET_PART_TYPES = {"gear", "flange", "ring", "cross"}


def web_thickness_mm(hole_d: float, forging_h: float) -> float:
    if hole_d <= 0:
        return 0.0
    s = 0.45 * math.sqrt(hole_d) - 0.25 * math.sqrt(forging_h) + 0.6 * math.sqrt(forging_h)
    return max(s, 4.0)  # толщина перемычки под прошивку, мм


def calculate(part: Part, forging: ForgingResult, process: str, heating: str) -> BlankResult:
    res = BlankResult()
    rho = loader.density()
    mass_forging = forging.mass_actual.value if forging.mass_actual else 0.0

    if process in ("die_forging_hammer", "die_forging_press"):
        pct = loader.flash_percent(forging.complexity)
        res.mass_flash = Quantity(
            mass_forging * pct / 100.0, "кг", SRC_LOSSES, False,  # масса облоя, кг
            f"Мобл = Мпок * {pct}% (по степени сложности {names.gost_code(forging.complexity)})",
        )
    else:
        res.mass_flash = Quantity(0.0, "кг", "Безоблойный процесс", True, "")

    hole_d = 0.0
    hole_h = 0.0
    for p in forging.primitives:
        if p.subtract and p.kind == "cylinder":
            hole_d = max(hole_d, p.d)
            hole_h = max(hole_h, p.l)
        if p.kind == "tube" and p.d_inner:
            hole_d = max(hole_d, p.d_inner)
            hole_h = max(hole_h, p.l)
    if hole_d > 0:
        s = web_thickness_mm(hole_d, hole_h)
        v_web = math.pi * hole_d ** 2 / 4.0 * s  # объём выдры, мм3
        res.mass_web = Quantity(
            v_web / geometry.MM3_IN_M3 * rho, "кг", SRC_LOSSES, False,
            f"Мвыдры = pi*d^2/4*s, d={hole_d:.0f} мм, s={s:.1f} мм",
        )
    else:
        res.mass_web = Quantity(0.0, "кг", "Отверстие не прошивается", True, "")

    key = "induction" if heating == "induction" else "flame_first"
    pct_scale = loader.scale_percent(key)
    base = mass_forging + res.mass_flash.value + res.mass_web.value  # масса до угара и резки, кг
    res.mass_scale = Quantity(
        base * pct_scale / 100.0, "кг", SRC_LOSSES, False,  # масса угара, кг
        f"Мугар = (Мпок+Мобл+Мвыдры) * {pct_scale}%",
    )

    pct_cut = float(loader.losses()["cut_loss_percent"])  # потери на резку, % от массы
    res.mass_cut_loss = Quantity(
        base * pct_cut / 100.0, "кг", SRC_LOSSES, False, f"Мрезки = {pct_cut}% от массы"
    )

    mass_blank = base + res.mass_scale.value + res.mass_cut_loss.value  # масса заготовки, кг
    res.mass_blank = Quantity(
        mass_blank, "кг", "Сумма составляющих", False,
        "Мзаг = Мпок + Мобл + Мвыдры + Мугар + Мрезки",
    )
    v_blank = mass_blank / rho * geometry.MM3_IN_M3  # объём заготовки, мм3
    res.volume_blank = Quantity(v_blank, "мм3", "Расчёт", False, "Vзаг = Мзаг / плотность")

    dims = geometry.overall(forging.primitives)
    stock = loader.stock_diameters()
    src_stock = loader.source_of("stock_round.json")

    if part.part_type in UPSET_PART_TYPES:
        best = None
        for d in stock:
            area = math.pi * d ** 2 / 4.0  # площадь сечения прутка, мм2
            length = v_blank / area  # длина заготовки, мм
            ratio = length / d  # отношение L/D
            if UPSET_RATIO_MIN <= ratio <= UPSET_RATIO_MAX:
                score = abs(ratio - UPSET_RATIO_TARGET)
                if best is None or score < best[0]:
                    best = (score, d, length, ratio)
        if best is None:
            d = min(stock, key=lambda x: abs(v_blank / (math.pi * x ** 2 / 4.0) / x - UPSET_RATIO_TARGET))
            area = math.pi * d ** 2 / 4.0
            length = v_blank / area
            ratio = length / d
            res.warnings.append(
                "Не удалось подобрать диаметр с отношением L/D в пределах 1,25...2,5 — "
                "проверить применимость осадки"
            )
        else:
            _, d, length, ratio = best
        reason = "Заготовка под осадку: подбор по отношению L/D"
    else:
        target_len = max(dims["length_mm"], 1.0)
        area_needed = v_blank / target_len
        d_calc = math.sqrt(4 * area_needed / math.pi)  # расчётный диаметр прутка, мм
        d = next((x for x in stock if x >= d_calc), stock[-1])
        area = math.pi * d ** 2 / 4.0
        length = v_blank / area
        ratio = length / d
        reason = "Заготовка под штамповку/протяжку: сечение из объёма и длины поковки"

    if process == "die_forging_hammer":
        steps = stack(part.primitives, forging.primitives)
        for p, q in steps:
            if round(q.d, 1) != d:
                res.preform.append({"pass": "протяжка" if q.d < d else "подкатка",
                                    "element": p.name,
                                    "from_mm": d, "to_mm": round(q.d, 1)})
        if not steps and part.part_type not in UPSET_PART_TYPES:
            for p, q in zip(part.primitives, forging.primitives):
                if p.subtract or (q.kind != "box" and q.along_axis):
                    continue
                if q.kind == "box":
                    small, middle, _ = sorted((q.a, q.b, q.c))
                    a, b = middle, small
                else:
                    a, b = q.d, q.l
                d_eq = math.sqrt(4 * a * b / math.pi)  # диаметр, равновеликий сечению элемента поперёк оси, мм
                res.preform.append({"pass": "протяжка" if d_eq < d else "подкатка",
                                    "element": p.name, "from_mm": d, "to_mm": round(d_eq, 1),
                                    "section": f"{round(a, 1):g}×{round(b, 1):g}"})

    res.diameter = Quantity(d, "мм", f"{src_stock}; {reason}", False, "Ближайший размер сортамента")
    res.length = Quantity(length, "мм", "Расчёт", False, "Lзаг = Vзаг / Fзаг")
    if part.part_type in UPSET_PART_TYPES:
        res.upset_ratio = Quantity(ratio, "", "Условие устойчивости при осадке L/D <= 2,5", False, "L/D")

    area_blank = math.pi * d ** 2 / 4.0
    if part.part_type in UPSET_PART_TYPES:
        area_forging, _ = geometry.plan(forging.primitives, upright=True)
        y = area_forging / area_blank if area_blank else 0.0  # уковка при осадке y = Fпок / Fзаг
        formula = "y = Fпок / Fзаг (осадка)"
    else:
        sections = []
        for p in forging.primitives:
            if p.subtract:
                continue
            if p.kind in ("cylinder", "tube") and p.d:
                sections.append(math.pi * (p.d ** 2 - p.d_inner ** 2) / 4.0)
            elif p.kind == "box":
                a, b, c = sorted((p.a, p.b, p.c))
                sections.append(a * b)
        area_min = min(sections) if sections else area_blank
        y = area_blank / area_min if area_min else 1.0  # уковка при протяжке y = Fзаг / Fмин
        formula = "y = Fзаг / Fмин.сеч (протяжка/штамповка в открытом штампе)"
    res.forge_ratio = Quantity(
        y, "", "Ковка и штамповка: справочник под ред. Е.И. Семёнова", False, formula
    )
    if y < 1.3:
        res.warnings.append(
            f"Коэффициент уковки {y:.2f} < 1,3 — проработка структуры металла недостаточна"
        )

    mass_part = forging.mass_part.value if forging.mass_part else 0.0
    kim = mass_part / mass_blank * 100.0 if mass_blank else 0.0  # доля металла в детали, %
    res.material_utilization = Quantity(
        kim, "%", "Расчёт", False, "Доля металла в детали = Мдетали / Мзаготовки * 100%"
    )
    if mass_part:
        res.consumption_ratio = Quantity(
            mass_blank / mass_part, "", "Заводская норма расхода металла; в ГОСТ 7505-89 "
            "не нормируется", False, "КИМ = Мзаготовки / Мдетали",
        )

    if kim < 35:
        res.warnings.append(f"Низкий КИМ ({kim:.0f}%) — пересмотреть припуски и раскрой")

    res.forgings_per_blank = Quantity(
        1, "шт", "Маршрут движка: заготовка мерная, из неё получают одну поковку",
        True, "КП",
    )

    if mass_forging:
        res.forging_utilization = Quantity(
            mass_part / mass_forging, "", "ГОСТ 3.1403-85, таблица 3, графа 32",  # КИП = Мдетали / Мпоковки
            True, "КИП = Мдетали / Мпоковки",
        )

    elements = [
        ("Поковка", forging.mass_actual),
        ("Облой", res.mass_flash),
        ("Выдра", res.mass_web),
        ("Угар", res.mass_scale),
        ("Некратность", res.mass_cut_loss),
    ]
    for name, quantity in elements:
        if quantity is None or quantity.value <= 0:
            continue
        share = quantity.value / mass_blank * 100.0 if mass_blank else 0.0  # доля элемента в массе заготовки, %
        res.material_items.append(MaterialItem(
            name=name,
            mass=quantity,
            percent=Quantity(
                share, "%", "ГОСТ 3.1403-85, таблица 3, графа 35", True,
                "доля элемента в норме расхода (массе заготовки)",
            ),
        ))

    return res
