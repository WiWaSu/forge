from __future__ import annotations

from typing import Any

from ..calc.forging import genitive
from ..data import loader
from .. import names
from ..models import CalcResult

UPSET_PART_TYPES = {"gear", "flange", "ring", "cross"}
ELONGATED_PART_TYPES = {"shaft", "lever", "combined"}
FLASH_PROCESSES = {"die_forging_hammer", "die_forging_press"}


TUMBLED_PART_TYPES = {"gear", "flange", "ring"}


def lower_first(text: str) -> str:
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def preform_text(calc: CalcResult, kind: str) -> str:
    items = [f"{genitive(x['element']) if x['element'] else 'ступени'} до "
             + (f"сечения {x['section']} мм" if x.get("section") else f"⌀{x['to_mm']:g} мм")
             for x in calc.blank.preform if x["pass"] == kind]
    return ", ".join(items[:-1]) + " и " + items[-1] if len(items) > 1 else "".join(items)


def preform_lines(calc: CalcResult) -> list[str]:
    d = calc.blank.diameter.value if calc.blank.diameter else 0
    draw, roll = preform_text(calc, "протяжка"), preform_text(calc, "подкатка")
    return ([f"Протяжной: участки {draw}, из ⌀{d:g} мм"] if draw else []) + (
        [f"Подкатной: набор металла на участок {roll}"] if roll else [])


def clean_equipment(calc: CalcResult) -> str:
    part_type = calc.part.part_type if calc.part else ""
    return "tumbling" if part_type in TUMBLED_PART_TYPES else "shot_blast"


def treat_name(kind: str) -> str:
    if kind.startswith("отжиг"):
        return "Термическая (отжиг)"
    if "отпуск" in kind:
        return "Термическая (нормализация и отпуск)"
    return "Термическая (нормализация)"


def operation_name(code: str, equipment: str, calc: CalcResult | None = None) -> str:
    for op in loader.operations():
        if op["code"] != code:
            continue
        if code == "060_clean" and equipment == "tumbling":
            return "Очистная (галтовка)"
        if code == "045_trim" and calc is not None and calc.blank.mass_web and calc.blank.mass_web.value > 0:
            return "Обрезная (обрезка облоя и прошивка перемычки)"
        if code == "055_heat_treat" and calc is not None and calc.heating.treat_kind:
            return treat_name(calc.heating.treat_kind)
        return str(op["name_ru"])
    return code


def _equipment_for(code: str, calc: CalcResult) -> str:
    main = calc.force.main_equipment.type if calc.force.main_equipment else ""
    trim = calc.force.trim_equipment.type if calc.force.trim_equipment else "press_trim"

    if code == "005_cut":
        return "saw"
    if code == "010_heat":
        return "furnace_induction" if calc.heating.heater == "induction" else "furnace_flame"
    if code == "045_trim":
        return trim
    if code == "050_straighten":
        return trim or main
    if code == "055_heat_treat":
        return "furnace_heat_treat"
    if code == "060_clean":
        return clean_equipment(calc)
    if code in ("070_control", "075_mark"):
        return ""
    return main


def _needed(code: str, calc: CalcResult) -> tuple[bool, str]:
    part = calc.part
    assert part is not None
    process = calc.process
    f, b = calc.forging, calc.blank
    has_hole = bool(b.mass_web and b.mass_web.value > 0)

    if code == "005_cut":
        d = b.diameter.value if b.diameter else 0
        l = b.length.value if b.length else 0
        return True, f"Получение мерной заготовки из круга {d:g} мм длиной {l:.0f} мм"

    if code == "010_heat":
        t1 = calc.heating.temp_start.value if calc.heating.temp_start else 0
        return True, f"Нагрев под ковку до {t1:.0f} °C, сталь {part.material_grade}"

    if code == "015_upset":
        if part.part_type in UPSET_PART_TYPES:
            ratio = b.upset_ratio.value if b.upset_ratio else 0
            return True, (
                f"Круглая в плане поковка: осадка заготовки, отношение L/D = {ratio:.2f} "
                f"в пределах устойчивости"
            )
        return False, ""

    if code == "020_draw":
        if process == "free_forging" and part.part_type in ELONGATED_PART_TYPES:
            return True, "Удлинённая поковка, свободная ковка: протяжка — основная формообразующая операция"
        return False, ""

    if code == "025_preform":
        if process == "die_forging_hammer" and b.preform:
            draw, roll = preform_text(calc, "протяжка"), preform_text(calc, "подкатка")
            steps = ([f"протяжка участков {draw} из ⌀{b.diameter.value:g} мм"] if draw else []) + (
                [f"подкатка с набором металла на участок {roll}"] if roll else [])
            kind = "Ступенчатая" if calc.part.part_type == "shaft" else "Удлинённая"
            return True, f"{kind} поковка в многоручьевом штампе: " + ", ".join(steps)
        if process == "die_forging_hammer":
            return True, "Молотовая штамповка: заготовительный и предварительный ручьи многоручьевого штампа"
        if process == "die_forging_press" and f.complexity in ("C3", "C4"):
            return True, (f"Степень сложности {names.gost_code(f.complexity)}: "
                          f"без предварительного ручья металл не заполнит полость")
        return False, ""

    if code == "030_finish":
        if process == "free_forging":
            return False, ""
        eq = calc.force.main_equipment
        where = lower_first(eq.name_ru) if eq else "основное оборудование"
        return True, f"Окончательное формообразование поковки в окончательном ручье, оборудование — {where}"

    if code == "035_pierce":
        if has_hole and process == "free_forging":
            return True, "В поковке центральное отверстие: прошивка на прессе"
        return False, ""

    if code == "045_trim":
        if process in FLASH_PROCESSES:
            m = b.mass_flash.value if b.mass_flash else 0
            if has_hole:
                return True, f"Обрезка облоя {m:.2f} кг и прошивка перемычки {b.mass_web.value:.2f} кг"
            return True, f"Штамповка в открытом штампе: обрезка облоя массой {m:.2f} кг"
        return False, ""

    if code == "050_straighten":
        if part.part_type in ELONGATED_PART_TYPES:
            return True, "Удлинённая поковка коробится при остывании: правка в горячем состоянии"
        return False, ""

    if code == "055_heat_treat":
        h = calc.heating
        kind = h.treat_kind[:1].upper() + h.treat_kind[1:]
        if h.treat_temp_min and h.treat_temp_max:
            return True, (f"{kind} {h.treat_temp_min.value:g}–{h.treat_temp_max.value:g} °C {h.treat_cooling} "
                          f"под механическую обработку, сталь {part.material_grade}")
        return True, f"{kind}: режим устанавливает изготовитель (ГОСТ 8479-70, п. 1.16), сталь {part.material_grade}"

    if code == "060_clean":
        return True, "Удаление окалины перед контролем и механической обработкой"

    if code == "065_calibrate":
        if process == "die_forging_press" and f.accuracy_class in ("T1", "T2"):
            return True, f"Класс точности {names.gost_code(f.accuracy_class)} достигается калибровкой"
        return False, ""

    if code == "070_control":
        m = f.mass_actual.value if f.mass_actual else 0
        return True, f"Контроль размеров по чертежу поковки и массы поковки {m:.2f} кг"

    if code == "075_mark":
        return True, "Маркирование номера плавки и обозначения поковки"

    return False, ""


def build(calc: CalcResult) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    number = 0

    for op in loader.operations():
        code = op["code"]
        if calc.process not in op["processes"]:
            continue
        needed, rationale = _needed(code, calc)
        if not needed:
            continue
        number += 5
        equipment = _equipment_for(code, calc)
        name = operation_name(code, equipment, calc)
        operations.append({
            "number": f"{number:03d}",
            "code": code,
            "name": name,
            "equipment": equipment,
            "rationale": rationale,
        })

    part = calc.part
    eq = calc.force.main_equipment
    summary = (
        f"{part.name if part else 'Деталь'}: {len(operations)} операций, "
        f"{eq.name_ru + ' ' + format(eq.value, 'g') + ' ' + eq.unit if eq else 'оборудование не выбрано'}. "
        f"{calc.process_reason}"
    )

    return {"process": calc.process, "operations": operations, "summary": summary}
