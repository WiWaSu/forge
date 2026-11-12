from __future__ import annotations

import json
from .. import paths
from typing import Any

from ..data import loader
from ..models import CalcResult
from .baseline import preform_lines

INSTRUCTIONS_DIR = paths.instructions()
EXAMPLES_DIR = INSTRUCTIONS_DIR / "examples"


def instruction(name: str) -> str:
    data = json.loads((INSTRUCTIONS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return "\n".join(data["text"]).strip()


def system_prompt() -> str:
    return instruction("system")


def render_calc(calc: CalcResult, detailed: bool = False) -> str:
    part = calc.part
    assert part is not None
    f, b, h, fr = calc.forging, calc.blank, calc.heating, calc.force

    def num(q: Any, digits: int = 2) -> str:
        return "—" if q is None else f"{q.value:.{digits}f} {q.unit}".strip()

    lines = [
        "ДЕТАЛЬ",
        f"  Наименование: {part.name}",
        f"  Обозначение: {part.designation or '—'}",
        f"  Тип: {part.part_type}",
        f"  Материал: {part.material_grade} (группа {f.steel_group})",
        f"  Программа выпуска: {part.annual_program} шт./год",
        f"  Шероховатость поверхностей: Ra {part.roughness_ra_um}",
        "",
        "ТЕХПРОЦЕСС (определён расчётом, менять нельзя)",
        f"  Код: {calc.process}",
        f"  Обоснование: {calc.process_reason}",
        "",
        "ПОКОВКА",
        f"  Масса детали: {num(f.mass_part)}",
        f"  Масса поковки: {num(f.mass_actual)}",
        *([f"  Припуски: {f.norm}", f"  Тип поковки: {f.forging_kind}"] if f.forging_kind else [
            f"  Степень сложности: {f.complexity}",
            f"  Класс точности: {f.accuracy_class}",
            f"  Исходный индекс: {f.initial_index}",
        ]),
        *(["  Размеры поковки, мм (деталь → поковка):"]
          + [f"    {r['label']}: {r['part_mm']:g} → {r['forging_mm']:g}" for r in f.dimensions]
          if detailed else []),
        "",
        "ЗАГОТОВКА",
        f"  Сортамент: круг {b.diameter.value:g} мм x {b.length.value:.0f} мм"
        if b.diameter and b.length else "  Сортамент: —",
        f"  Масса заготовки: {num(b.mass_blank)}",
        f"  Масса облоя: {num(b.mass_flash)}",
        f"  Масса угара (окалины): {num(b.mass_scale)}",
        f"  Масса выдры (перемычки): {num(b.mass_web)}"
        + ("  <- отверстие прошивается" if b.mass_web and b.mass_web.value > 0
           else "  <- отверстие не прошивается"),
        f"  Отношение L/D заготовки до осадки: {b.upset_ratio.value:.2f}" if b.upset_ratio else "",
        "",
        *(["", "ЗАГОТОВИТЕЛЬНЫЕ РУЧЬИ (по расчёту)"] + [f"  {line}" for line in preform_lines(calc)]
          if b.preform else []),
        "",
        "НАГРЕВ",
        f"  Интервал ковки: {num(h.temp_start, 0)} — {num(h.temp_end, 0)} (ниже конца не штампуют)",
        f"  Выдержка заготовки в печи: {num(h.hold_time, 0)}" if detailed and h.hold_time else "",
        f"  Выдержка поковки при термообработке: {num(h.treat_hold_time, 0)}"
        if detailed and h.treat_hold_time else "",
        (f"  Термообработка поковки: {h.treat_kind}, {h.treat_temp_min.value:g}–{h.treat_temp_max.value:g} °C, "
         f"охлаждение {h.treat_cooling}" if h.treat_temp_min and h.treat_temp_max else
         f"  Термообработка поковки: {h.treat_kind}, температуру назначает технолог")
        if detailed and h.treat_kind else "",
        f"  Нагреватель: {h.heater} ({h.heater_name})",
        "",
        "ОБОРУДОВАНИЕ (выбрано расчётом, менять нельзя)",
    ]

    if fr.main_equipment:
        e = fr.main_equipment
        lines.append(f"  Основное: {e.type} — {e.name_ru}, {e.value:g} {e.unit}")
    if fr.trim_equipment:
        e = fr.trim_equipment
        lines.append(f"  Обрезное: {e.type} — {e.name_ru}, {e.value:g} {e.unit}")

    return "\n".join(line for line in lines if line != "")


def render_allowed_operations(process: str) -> str:
    equipment_names = {e["type"]: e["name_ru"] for e in loader.load("equipment.json")["equipment"]}
    lines = ["ДОПУСТИМЫЕ ОПЕРАЦИИ (только эти коды, копировать посимвольно)"]
    for op in loader.operations():
        if process not in op["processes"]:
            continue
        eq = op["equipment"]
        if eq:
            eq_text = "; оборудование: " + ", ".join(
                f"{code} ({equipment_names.get(code, code)})" for code in eq
            )
        else:
            eq_text = "; оборудование не требуется, указывать пустую строку"
        lines.append(f"  {op['code']} — {op['name_ru']}{eq_text}")
    return "\n".join(lines)


def load_examples(limit: int = 2) -> list[dict[str, Any]]:
    if not EXAMPLES_DIR.exists():
        return []
    examples = []
    for path in sorted(EXAMPLES_DIR.glob("route_*.json"))[:limit]:
        with path.open(encoding="utf-8") as f:
            examples.append(json.load(f))
    return examples


def build_route_messages(calc: CalcResult, use_examples: bool = True) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt()}]

    task = instruction("route_card")

    if use_examples:
        for ex in load_examples():
            messages.append({"role": "user", "content": ex["input"]})
            messages.append({
                "role": "assistant",
                "content": json.dumps(ex["output"], ensure_ascii=False),
            })

    messages.append({
        "role": "user",
        "content": (
            f"{task}\n\n"
            f"{render_allowed_operations(calc.process)}\n\n"
            f"РАСЧЁТ\n{render_calc(calc)}\n\n"
            f"Построй маршрут. Ответ — только JSON."
        ),
    })
    return messages
