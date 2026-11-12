from __future__ import annotations

from ..data import loader
from ..models import EquipmentChoice, ForceResult, ForgingResult, Part, Quantity

SRC_FORCE = "Ребельский А.В. Основы проектирования процессов горячей объёмной штамповки (требует сверки)"
SRC_HAMMER = "Брюханов А.Н. Ковка и объёмная штамповка: G = (3,5...6,3)*F, F в см2 (требует сверки)"

K_PRESS = 8.0
K_HAMMER = 5.6
TRIM_FORCE_SHARE = 0.15
FLASH_LAND_MM = 8.0


def _num(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _picked(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _pick(series: list[float], required: float, reserve: float = 1.10) -> float | None:
    target = required * reserve  # требуемое значение с запасом
    for value in series:
        if value >= target:
            return value
    return None


def calculate(part: Part, forging: ForgingResult, process: str) -> ForceResult:
    from . import geometry
    from .forging import ROUND_IN_PLAN

    res = ForceResult()
    mat = loader.material(part.material_grade)
    sigma_hot = float(mat["sigma_hot_mpa_1100"])

    die = process in ("die_forging_hammer", "die_forging_press")
    area_forging, perimeter = geometry.plan(forging.primitives, part.part_type in ROUND_IN_PLAN or not die)
    area_flash = perimeter * FLASH_LAND_MM if die else 0.0  # площадь мостика облоя, мм2
    area_total = area_forging + area_flash  # площадь проекции с облоем, мм2
    res.projection_area = Quantity(
        round(area_total, 0), "мм2", "Расчёт по размерам поковки", False,
        "F = Fпок + Fобл (мостик облойной канавки)",
    )

    area_cm2 = area_total / 100.0  # та же площадь, см2

    if process == "die_forging_press" or process == "die_forging_hcm":
        force_mn = K_PRESS * sigma_hot * (area_total / 1_000_000.0)  # усилие штамповки P = k * sigma_гор * F, МН
        res.force_required = Quantity(
            force_mn, "МН", SRC_FORCE, False,
            f"P = {K_PRESS} * sigma_гор({sigma_hot} МПа) * F",
        )
        eq_type = "press_crank" if process == "die_forging_press" else "hcm"
        eq = loader.equipment(eq_type)
        picked = _pick(eq["series"], force_mn)
        if picked is None:
            res.warnings.append(
                f"Требуемое усилие {force_mn:.1f} МН превышает наибольший типоразмер "
                f"{eq['name_ru']} ({eq['series'][-1]} МН)"
            )
        else:
            res.main_equipment = EquipmentChoice(
                type=eq_type, name_ru=eq["name_ru"], param="force_mn", value=picked, unit="МН",
                reason=_press_reason(eq_type, picked, force_mn),
            )

    elif process == "die_forging_hammer":
        mass_kg = K_HAMMER * area_cm2  # масса падающих частей G = k * F, кг
        res.falling_mass_required = Quantity(
            mass_kg, "кг", SRC_HAMMER, False, f"G = {K_HAMMER} * F({area_cm2:.0f} см2)"
        )
        eq = loader.equipment("hammer_die")
        picked = _pick(eq["series"], mass_kg, reserve=1.0)
        if picked is None:
            res.warnings.append(
                f"Требуемая масса падающих частей {mass_kg:.0f} кг превышает "
                f"наибольший типоразмер молота ({eq['series'][-1]} кг)"
            )
        else:
            res.main_equipment = EquipmentChoice(
                type="hammer_die", name_ru=eq["name_ru"], param="falling_mass_kg",
                value=picked, unit="кг",
                reason=(f"Выбран молот с массой падающих частей {_picked(picked)} кг — "
                        f"ближайший больший к расчётной {_num(mass_kg, 0)} кг"),
            )

    else:
        force_mn = 1.5 * sigma_hot * (area_total / 1_000_000.0)  # усилие осадки P = 1,5 * sigma_гор * F, МН
        res.force_required = Quantity(
            force_mn, "МН", SRC_FORCE, False, "P = 1,5 * sigma_гор * F (осадка на прессе)"
        )
        eq = loader.equipment("press_hydraulic")
        picked = _pick(eq["series"], force_mn)
        if picked:
            res.main_equipment = EquipmentChoice(
                type="press_hydraulic", name_ru=eq["name_ru"], param="force_mn",
                value=picked, unit="МН",
                reason=(f"Выбран гидравлический ковочный пресс усилием {_picked(picked)} МН — "
                        f"ближайший больший к расчётному {_num(force_mn, 1)} МН с запасом 10%"),
            )

    if process in ("die_forging_hammer", "die_forging_press"):
        if res.force_required:
            trim = res.force_required.value * TRIM_FORCE_SHARE  # усилие обрезки облоя, МН
        else:
            trim = perimeter * 3.0 * sigma_hot / 1_000_000.0  # усилие обрезки по площади среза, МН
        res.trim_force_required = Quantity(
            trim, "МН", SRC_FORCE, False,
            "Pобр = 0,15 * Pшт либо по площади среза облоя",
        )
        eq = loader.equipment("press_trim")
        picked = _pick(eq["series"], trim)
        if picked:
            res.trim_equipment = EquipmentChoice(
                type="press_trim", name_ru=eq["name_ru"], param="force_mn",
                value=picked, unit="МН",
                reason=(f"Выбран обрезной пресс усилием {_picked(picked)} МН — "
                        f"ближайший больший к расчётному усилию обрезки {_num(trim, 2)} МН"),
            )

    return res


def _press_reason(eq_type: str, picked: float, force_mn: float) -> str:
    tail = f"усилием {_picked(picked)} МН — "
    need = f"к расчётному {_num(force_mn, 1)} МН с запасом 10%"
    if eq_type == "hcm":
        return f"Выбрана горизонтально-ковочная машина {tail}ближайшая большая {need}"
    return f"Выбран кривошипный горячештамповочный пресс {tail}ближайший больший {need}"
