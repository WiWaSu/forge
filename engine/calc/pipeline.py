from __future__ import annotations

from typing import Any

from ..data import loader
from ..models import CalcResult, Part
from . import blank, force, forging, geometry, heating, sources

PROGRAM_SINGLE = 200
PROGRAM_MASS = 5_000
MASS_LIMIT_DIE_FORGING = 100

PROCESS_EQUIPMENT = {
    "die_forging_hammer": "hammer_die",
    "die_forging_press": "press_crank",
    "die_forging_hcm": "hcm",
    "free_forging": "hammer_forge",
}


def choose_process(part: Part, mass_forging_kg: float) -> tuple[str, str]:
    if mass_forging_kg > MASS_LIMIT_DIE_FORGING:
        return (
            "free_forging",
            f"Масса поковки {mass_forging_kg:.1f} кг превышает {MASS_LIMIT_DIE_FORGING} кг — "
            f"штамповка в закрепленном штампе нерентабельна",
        )
    if part.annual_program < PROGRAM_SINGLE:
        return (
            "free_forging",
            f"Программа выпуска {part.annual_program} шт./год — единичное производство, "
            f"изготовление штампа не окупается",
        )
    if part.annual_program >= PROGRAM_MASS:
        return (
            "die_forging_press",
            f"Программа выпуска {part.annual_program} шт./год — крупносерийное производство, "
            f"КГШП даёт выше производительность и точность",
        )
    return (
        "die_forging_hammer",
        f"Программа выпуска {part.annual_program} шт./год — серийное производство, "
        f"молотовая штамповка в многоручьевом штампе",
    )


def collect_non_standard(data: Any) -> list[str]:
    found: list[tuple[str, dict[str, Any]]] = []
    sources.walk(data, "", found)
    return [f"{path}: {q['value']} {q['unit']} ({q['source']})" for path, q in found]


def calculate(part: Part, process: str | None = None, accuracy: str | None = None) -> CalcResult:
    problems = loader.integrity()
    if problems:
        raise loader.ReferenceError("Справочники повреждены: " + "; ".join(problems))

    res = CalcResult(part=part)

    rho = loader.density()
    mass_part = part.mass_kg if part.mass_kg else geometry.mass(part.primitives, rho)
    kr = forging.kr_coefficient(part.part_type)
    mass_estimate = mass_part * kr.value  # оценка массы поковки Мд * Кр, кг

    if process:
        res.process = process
        res.process_reason = "Задано пользователем"
    else:
        res.process, res.process_reason = choose_process(part, mass_estimate)

    equipment_type = PROCESS_EQUIPMENT[res.process]

    material = loader.material(part.material_grade)
    res.material = {"grade": material.get("grade", part.material_grade),
                    "gost": material.get("gost", "")}
    res.stock = {"profile": "Круг", "gost": loader.source_of("stock_round.json").split(",")[0]}

    res.forging = forging.calculate(part, equipment_type, accuracy)
    res.heating = heating.calculate(part, mass_estimate)
    res.blank = blank.calculate(part, res.forging, res.process, res.heating.heater)
    res.force = force.calculate(part, res.forging, res.process)

    if res.blank.diameter:
        res.heating.hold_time = heating.hold_time(res.heating.heater, res.blank.diameter.value)

    thickness = forging.max_thickness(part.part_type, res.forging.primitives)
    res.heating.treat_hold_time = heating.hold_time("heat_treat", thickness)

    for block in (res.forging, res.blank, res.heating, res.force):
        res.warnings.extend(getattr(block, "warnings", []))

    if not loader.gost7505_filled():
        res.warnings.insert(
            0,
            "Числовые таблицы ГОСТ 7505-89 не заполнены — припуски, допуски и "
            "производные от них размеры получены по приближённой модели и "
            "НЕ ЯВЛЯЮТСЯ нормативными",
        )
    elif not loader.gost7505_verified():
        indices = loader.gost7505_indices() or [0]
        if res.forging.initial_index in indices:
            res.warnings.insert(
                0,
                f"Припуски и допуски взяты из таблиц ГОСТ 7505-89 (индекс "
                f"{res.forging.initial_index}), но источник — перепечатки стандарта, "
                f"сверенные между собой. Требуется выборочная сверка по оригиналу",
            )
        else:
            res.warnings.insert(
                0,
                f"Исходный индекс {res.forging.initial_index} вне диапазона "
                f"{min(indices)}...{max(indices)}, сверенного по двум источникам — "
                f"припуски и допуски требуют проверки по оригиналу стандарта",
            )

    as_dict = res.to_dict()
    res.non_standard_values = collect_non_standard(as_dict)
    res.non_standard = sources.non_standard(as_dict)
    res.normative = sources.normative(as_dict)
    return res
