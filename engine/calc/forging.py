from __future__ import annotations

from typing import Any

from ..data import loader
from .. import names
from ..models import ForgingResult, Part, Quantity
from . import free_forging, geometry

SRC_7505 = "ГОСТ 7505-89"

FREE_FORGING_EQUIPMENT = "hammer_forge"

ROUND_IN_PLAN = {"gear", "flange", "ring", "cross"}


def is_thickness(part_type: str, primitive: Any, key: str) -> bool:
    if primitive.kind == "box":
        return key in ("a", "b") if part_type == "shaft" else key == "c"
    upright = part_type in ROUND_IN_PLAN or not primitive.along_axis
    return key == "l" if upright else key in ("d", "d_inner")


def max_thickness(part_type: str, primitives: list) -> float:
    dims = geometry.overall(primitives)
    if part_type in ROUND_IN_PLAN:
        return dims["length_mm"]
    if part_type == "shaft":
        return dims["max_cross_mm"]
    values = [getattr(p, key) for p in primitives if not p.subtract
              for key in ("d", "l", "a", "b", "c")
              if getattr(p, key) and is_thickness(part_type, p, key)]
    return max(values, default=dims["max_cross_mm"])


def stack(part_prims: list, forged: list) -> list[tuple[Any, Any]]:
    steps = [(p, q) for p, q in zip(part_prims, forged) if not p.subtract and p.along_axis]
    return steps if len(steps) > 1 and all(p.kind != "box" for p, _ in steps) else []


def restack(part_prims: list, forged: list, allowance_fn) -> None:
    steps = stack(part_prims, forged)
    if not steps:
        return
    end = allowance_fn(sum(p.l for p, _ in steps), steps[0][0], "l")
    faces = [end] + [allowance_fn(max(a.l, b.l), a, "l") for (a, _), (b, _) in zip(steps, steps[1:])] + [end]
    for i, (p, q) in enumerate(steps):
        grow = 0.0
        for face, j in ((i, i - 1), (i + 1, i + 1)):
            other = steps[j][0].d if 0 <= j < len(steps) else 0.0
            grow += faces[face] * ((p.d > other) - (p.d < other))
        q.l = max(p.l + grow, 0.0)  # длина ступени поковки: припуск на каждый торец и уступ один раз, мм


def steel_group(grade: str) -> str:
    mat = loader.material(grade)
    c = float(mat["c_percent"])
    alloy = float(mat["alloy_percent"])
    for group in loader.steel_groups():
        c_max = group["c_max_percent"]
        a_max = group["alloy_max_percent"]
        if c_max is None or a_max is None:
            return group["code"]
        if c <= c_max and alloy <= a_max:
            return group["code"]
    return "M3"


def complexity(mass_forging_kg: float, mass_envelope_kg: float) -> str:
    if mass_envelope_kg <= 0:
        return "C1"
    ratio = min(mass_forging_kg / mass_envelope_kg, 1.0)  # степень сложности Мпок / Мфигуры
    for level in loader.complexity_levels():
        if level["ratio_min"] < ratio <= level["ratio_max"]:
            return level["code"]
    return "C4"


def accuracy_class(equipment_type: str, requested: str | None = None,
                   flame_heating: bool = False) -> str:
    rec = loader.accuracy_by_equipment(equipment_type)
    if requested:
        if requested not in rec["classes"]:
            raise loader.ReferenceError(
                f"Класс точности {requested} недопустим для оборудования "
                f"'{rec['name_ru']}'. По таблице 19 ГОСТ 7505-89 допустимы: "
                f"{', '.join(rec['classes'])}"
            )
        return requested

    chosen = rec["default"]
    if flame_heating and chosen in ("T2", "T3", "T4"):
        lowered = f"T{int(chosen[1]) + 1}"
        if lowered in rec["classes"] or lowered == "T5":
            chosen = lowered
    return chosen


def kr_coefficient(
    part_type: str,
    hole_formed_in_forging: bool = True,
    machined_all_over: bool = True,
) -> Quantity:
    if not hole_formed_in_forging:
        group = loader.kr_group("5")
    elif not machined_all_over:
        group = loader.kr_group("4")
    else:
        group = loader.kr_group_for_part_type(part_type)
    value = (group["kr_min"] + group["kr_max"]) / 2.0  # Кр, середина диапазона группы
    return Quantity(
        value=value,
        unit="",
        source=f"{SRC_7505}, приложение 3, таблица 20 (группа {group['code']}: "
               f"{group['kr_min']}...{group['kr_max']})",
        standard=loader.is_verified("kr_coefficients.json"),
        formula="Мп.р = Мд * Кр, Кр — середина диапазона по группе конфигурации",
    )


def initial_index(mass_calc_kg: float, group: str, cmplx: str, accuracy: str) -> tuple[int, bool]:
    table = loader.gost7505()["initial_index"]
    base = None
    for row in table["base_by_mass_kg"]:
        if mass_calc_kg <= row["mass_max"]:
            base = row["base"]
            break
    if base is None:
        base = table["base_by_mass_kg"][-1]["base"]
    index = (
        base
        + table["shift_steel_group"][group]
        + table["shift_complexity"][cmplx]
        + table["shift_accuracy"][accuracy]
    )
    index = max(table["index_min"], min(table["index_max"], index))
    return index, bool(table.get("verified"))


def _column(ranges: list, size: float) -> int:
    for i, upper in enumerate(ranges):
        if upper is None or size <= upper:
            return i
    return len(ranges) - 1


def _clamp_column(block: dict, index: int, column: int) -> int:
    limit = block.get("max_column_by_index", {}).get(str(index), block.get("max_column_default", 8))
    return min(column, limit)


def roughness_column(ra_um: float) -> str:
    for col in loader.gost7505()["main_allowances"]["roughness_columns"]:
        lo, hi = col["ra_min_um"], col["ra_max_um"]
        if hi is None:
            if ra_um >= lo:
                return col["code"]
        elif lo < ra_um <= hi:
            return col["code"]
    return "ra125"


def main_allowance(index: int, size_mm: float, thickness_mm: float,
                   ra_um: float = 6.3, is_thickness: bool = False) -> Quantity:
    ma = loader.gost7505()["main_allowances"]
    col_name = roughness_column(ra_um)
    series = ma["allowance_series"][col_name]

    if is_thickness:
        column = _column(ma["thickness_ranges_mm"], thickness_mm)
        axis = "толщина"
    else:
        column = _column(ma["size_ranges_mm"], size_mm)
        axis = "длина/диаметр"
    column = _clamp_column(ma, index, column)

    pos = (index - 1) + column  # позиция в ряду ГОСТ 7505-89
    if 0 <= pos < len(series):
        return Quantity(
            value=series[pos],
            unit="мм",
            source=f"{SRC_7505}, таблица 3 (индекс {index}, {axis}, столбец {column}, {col_name})",
            standard=True,
            formula=f"позиция в ряду = (индекс - 1) + столбец = {pos}",
        )

    fb = loader.fallback_allowances()["allowance"]
    value = min(max(fb["z0"] + fb["k_size"] * size_mm + fb["k_index"] * (index - 1),  # приближённый припуск, мм
                    fb["min"]), fb["max"])
    return Quantity(
        value=round(value * 2) / 2, unit="мм",
        source="ПРИБЛИЖЁННАЯ модель (allowances_fallback.json), НЕ ГОСТ: размер вне таблицы 3",
        standard=False, formula=loader.fallback_allowances()["_meta"]["model"],
    )


def flatness_allowance(accuracy: str, overall_mm: float) -> Quantity:
    block = loader.gost7505()["additional_allowances"]["flatness"]
    column = _column(block["size_ranges_mm"], overall_mm)
    value = block["values"][accuracy][min(column, len(block["values"][accuracy]) - 1)]
    return Quantity(
        value=value, unit="мм",
        source=f"{SRC_7505}, таблица 5 (класс {accuracy}, наибольший размер поковки)",
        standard=True, formula="дополнительный припуск на толщину",
    )


def linear_tolerance(index: int, size_mm: float, is_thickness: bool = False,
                     max_thickness_mm: float = 0.0) -> dict[str, Quantity]:
    lt = loader.gost7505()["linear_tolerances"]
    series = lt["tolerance_series"]

    if is_thickness:
        column = _column(lt["thickness_ranges_mm"], max_thickness_mm or size_mm)
        axis = "наибольшая толщина поковки"
    else:
        column = _column(lt["size_ranges_mm"], size_mm)
        axis = "длина/диаметр"
    column = _clamp_column(lt, index, column)

    pos = (index - 1) + column  # позиция в ряду ГОСТ 7505-89
    if 0 <= pos < len(series):
        tol = series[pos]
        up, low = lt["deviations"][str(tol)]
        src = f"{SRC_7505}, таблица 8 (индекс {index}, {axis}, столбец {column})"
        return {
            "tolerance": Quantity(tol, "мм", src, True),
            "upper": Quantity(up, "мм", src, True),
            "lower": Quantity(low, "мм", src, True),
        }

    fb = loader.fallback_allowances()
    t = fb["tolerance"]
    value = min(max(t["t0"] + t["kt_size"] * size_mm + t["kt_index"] * (index - 1),  # приближённый допуск, мм
                    t["min"]), t["max"])
    split = fb["tolerance_split"]
    src = "ПРИБЛИЖЁННАЯ модель (allowances_fallback.json), НЕ ГОСТ: размер вне таблицы 8"
    return {
        "tolerance": Quantity(round(value, 1), "мм", src, False),
        "upper": Quantity(round(value * split["plus_share"], 1), "мм", src, False),
        "lower": Quantity(-round(value * split["minus_share"], 1), "мм", src, False),
    }


_GENITIVE_TAIL = (
    ("ец", "ца"),
    ("ка", "ки"), ("га", "ги"), ("ха", "хи"), ("жа", "жи"),
    ("ча", "чи"), ("ша", "ши"), ("ща", "щи"),
    ("а", "ы"),
    ("я", "и"),
    ("ие", "ия"),
    ("о", "а"), ("е", "а"),
    ("ь", "я"),
    ("й", "я"),
)

_ADJECTIVE_TAIL = (("ая", "ой"), ("яя", "ей"), ("ое", "ого"), ("ее", "его"),
                   ("ый", "ого"), ("ий", "его"), ("ой", "ого"))


def _adjective(word: str) -> str | None:
    low = word.lower()
    if len(low) < 4:
        return None
    for tail, replacement in _ADJECTIVE_TAIL:
        if low.endswith(tail):
            return word[: len(word) - len(tail)] + replacement
    return None


def genitive(word: str) -> str:
    parts = word.split()
    if not parts:
        return word
    feminine = any(p.lower().endswith(("ая", "яя")) for p in parts)
    out: list[str] = []
    noun_done = False
    for p in parts:
        adj = _adjective(p) if len(parts) > 1 else None
        if adj is not None:
            out.append(adj)
            continue
        if noun_done:
            out.append(p)
            continue
        noun_done = True
        low = p.lower()
        if feminine and low.endswith("ь"):
            out.append(p[:-1] + "и")
            continue
        for tail, replacement in _GENITIVE_TAIL:
            if low.endswith(tail):
                out.append(p[: len(p) - len(tail)] + replacement)
                break
        else:
            out.append(p + "а")
    return " ".join(out)


MAX_INDEX_PASSES = 3


def dimension_label(primitive: Any, key: str, label: str) -> str:
    if primitive.subtract:
        if key in ("d", "d_inner"):
            return "внутренний диаметр"
        return "глубина отверстия" if key == "l" else "толщина"
    name = (primitive.name or "").strip()
    return f"{label} {genitive(name)}" if name else label


def calculate(part: Part, equipment_type: str, accuracy_requested: str | None = None) -> ForgingResult:
    res = ForgingResult()
    rho = loader.density()

    mass_part = part.mass_kg if part.mass_kg else geometry.mass(part.primitives, rho)
    res.mass_part = Quantity(
        mass_part, "кг",
        "Задано пользователем" if part.mass_kg else "Расчёт по геометрии детали",
        True, "M = V * плотность",
    )

    hole_formed = True
    kr = kr_coefficient(
        part.part_type,
        hole_formed_in_forging=hole_formed,
        machined_all_over=part.machined_all_over,
    )
    res.kr = kr
    mass_calc = mass_part * kr.value  # расчётная масса поковки Мп.р = Мд * Кр, кг
    res.mass_calc = Quantity(
        mass_calc, "кг", f"{SRC_7505}, п.2.6 и приложение 3", kr.standard, "Мп.р = Мд * Кр"
    )

    res.steel_group = steel_group(part.material_grade)

    fallback = False
    if equipment_type == FREE_FORGING_EQUIPMENT:
        try:
            return free_forging.calculate(part, res)
        except free_forging.Unsupported as exc:
            fallback = True
            res.dimensions = []
            res.warnings.append(
                f"Свободная ковка: {exc}. Припуски назначены по ГОСТ 7505-89 как приближение — "
                f"для свободной ковки это не норматив, значения помечены «не ГОСТ»")
            res.norm = f"{SRC_7505} — приближение: форма вне таблицы 1 ГОСТ 7829-70"

    factor = loader.gost7505()["complexity_note"]["envelope_factor"]
    dims_part = geometry.overall(part.primitives)
    v_env = geometry.envelope_volume(part.primitives) * factor ** 3  # объём описанной фигуры, мм3
    mass_env = v_env / geometry.MM3_IN_M3 * rho  # масса описанной фигуры, кг
    res.accuracy_class = accuracy_class(
        equipment_type, accuracy_requested, flame_heating=(part.heating == "flame")
    )

    overall_size = max(dims_part["max_cross_mm"], dims_part["length_mm"])
    extra = flatness_allowance(res.accuracy_class, overall_size)

    def shape_for(index: int) -> tuple[list, dict, float, float]:
        def allowance_fn(size: float, primitive: Any, key: str) -> float:
            if size <= 0 or not part.machined_all_over:
                return 0.0
            thick = is_thickness(part.part_type, primitive, key)
            z = main_allowance(index, size, size, part.roughness_ra_um, thick).value
            return z + (extra.value if thick else 0.0)

        prims = geometry.scale_primitives(part.primitives, allowance_fn)
        restack(part.primitives, prims, allowance_fn)
        dims = geometry.overall(prims)
        return prims, dims, max_thickness(part.part_type, prims), geometry.mass(prims, rho)

    mass_for_index = mass_calc
    res.index_basis = "ориентировочная масса Мд·Кр (ГОСТ 7505-89, п. 2.6)"
    index = 0
    index_verified = True
    for attempt in range(1, MAX_INDEX_PASSES + 1):
        res.complexity = complexity(mass_for_index, mass_env)
        index, index_verified = initial_index(
            mass_for_index, res.steel_group, res.complexity, res.accuracy_class
        )
        res.primitives, dims_forge, forge_thickness, mass_actual = shape_for(index)
        res.index_passes = attempt
        refined, _ = initial_index(
            mass_actual, res.steel_group, complexity(mass_actual, mass_env), res.accuracy_class
        )
        if refined == index:
            break
        mass_for_index = mass_actual
        res.index_basis = "масса поковки по номинальным размерам (ГОСТ 7505-89, п. 2.6)"

    res.initial_index = index
    if not index_verified:
        res.warnings.append("Правило исходного индекса не сверено с оригиналом стандарта")

    def add_row(element: str, parameter: str, label: str, nominal_part: float, nominal_forge: float,
                thick: bool, inner: bool) -> None:
        tol = linear_tolerance(index, nominal_forge, thick, forge_thickness)
        res.dimensions.append({
            "element": element,
            "parameter": parameter,
            "label": label,
            "part_mm": round(nominal_part, 2),
            "allowance_mm": round((nominal_part - nominal_forge if inner else nominal_forge - nominal_part) / 2.0, 2),
            "forging_mm": round(nominal_forge, 1),
            "tolerance": tol["tolerance"].to_dict(),
            "upper": tol["upper"].to_dict(),
            "lower": tol["lower"].to_dict(),
        })

    through = dims_part["length_mm"]
    for p_part, p_forge in zip(part.primitives, res.primitives):
        for key, label in (("d", "внешний диаметр"), ("d_inner", "внутренний диаметр"),
                           ("l", "высота"), ("a", "длина"), ("b", "ширина"), ("c", "высота")):
            nominal_part = getattr(p_part, key)
            if not nominal_part or (key == "l" and p_part.subtract and p_part.along_axis and nominal_part >= through):
                continue
            add_row(p_part.name or p_part.kind, label, dimension_label(p_part, key, label), nominal_part,
                    getattr(p_forge, key), is_thickness(part.part_type, p_part, key),
                    key == "d_inner" or (p_part.subtract and key == "d"))

    steps = stack(part.primitives, res.primitives)
    if steps:
        thick = is_thickness(part.part_type, steps[0][0], "l")
        label = "общая высота" if thick else "общая длина"
        add_row("поковка", label, label, sum(p.l for p, _ in steps), sum(q.l for _, q in steps), thick, False)

    res.mass_actual = Quantity(
        mass_actual, "кг", f"{SRC_7505}, п. 2.6: по номинальным размерам поковки",
        True, "M = V * плотность",
    )

    if mass_actual > 0 and abs(mass_calc - mass_actual) / mass_actual > 0.4:
        res.warnings.append(
            f"Ориентировочная масса Мд·Кр ({mass_calc:.1f} кг) расходится с массой поковки "
            f"по размерам ({mass_actual:.1f} кг) более чем на 40%: для типа детали "
            f"«{names.part_type_name(part.part_type)}» стоит проверить группу Кр таблицы 20. "
            f"На припуски это не влияет — индекс взят по размерам поковки"
        )
    if mass_actual > 250:
        res.warnings.append("Масса поковки свыше 250 кг — вне области действия ГОСТ 7505-89")
    if dims_part["max_cross_mm"] > 2500 or dims_part["length_mm"] > 2500:
        res.warnings.append("Габаритный размер свыше 2500 мм — вне области действия ГОСТ 7505-89")

    if fallback:
        for row in res.dimensions:
            for key in ("tolerance", "upper", "lower"):
                row[key]["standard"] = False
                row[key]["source"] = f"{row[key]['source']} (приближение для свободной ковки)"
        res.mass_actual.standard = False
    return res
