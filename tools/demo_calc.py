from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.calc import pipeline
from engine.models import Part

SAMPLES = Path(__file__).resolve().parents[1] / "engine" / "tests" / "parts_sample.json"


def q(value) -> str:
    if value is None:
        return "—"
    mark = "" if value.standard else "  [НЕ ГОСТ]"
    return f"{value.value:g} {value.unit}{mark}"


def report(part: Part) -> None:
    res = pipeline.calculate(part)
    f, b, h, fr = res.forging, res.blank, res.heating, res.force

    print("=" * 78)
    print(f"{part.name}   {part.designation}   сталь {part.material_grade}   "
          f"программа {part.annual_program} шт./год")
    print("=" * 78)

    print(f"\nТЕХПРОЦЕСС: {res.process}")
    print(f"  обоснование: {res.process_reason}")

    print("\nПОКОВКА")
    print(f"  Масса детали ................. {q(f.mass_part)}")
    print(f"  Коэффициент Кр ............... {q(f.kr)}")
    print(f"  Расчётная масса поковки ...... {q(f.mass_calc)}")
    print(f"  Масса поковки по размерам .... {q(f.mass_actual)}")
    print(f"  Группа стали / сложность ..... {f.steel_group} / {f.complexity}")
    print(f"  Класс точности / индекс ...... {f.accuracy_class} / {f.initial_index}")

    print("\n  Размеры поковки:")
    print(f"    {'элемент':<22}{'парам.':<10}{'деталь':>9}{'припуск':>9}"
          f"{'поковка':>10}{'допуск':>16}")
    for d in f.dimensions:
        tol = d["tolerance"]["value"]
        up, low = d["upper"]["value"], d["lower"]["value"]
        print(f"    {d['element'][:21]:<22}{d['parameter']:<10}{d['part_mm']:>9}"
              f"{d['allowance_mm']:>9}{d['forging_mm']:>10}"
              f"{f'{tol} (+{up}/{low})':>16}")

    print("\nНАГРЕВ")
    print(f"  Нагрев под ковку ............. {q(h.temp_start)}")
    print(f"  Интервал ковки ............... {q(h.temp_start)} — {q(h.temp_end)}")
    print(f"  Нагреватель .................. {h.heater_name}")
    print(f"  Угар ......................... {q(h.scale_percent)}")

    print("\nЗАГОТОВКА")
    print(f"  Облой ........................ {q(b.mass_flash)}")
    if b.mass_web and b.mass_web.value > 0:
        print(f"  Выдра (перемычка) ............ {q(b.mass_web)}")
    print(f"  Угар ......................... {q(b.mass_scale)}")
    print(f"  Потери при резке ............. {q(b.mass_cut_loss)}")
    print(f"  МАССА ЗАГОТОВКИ .............. {q(b.mass_blank)}")
    ratio = f"   (L/D = {b.upset_ratio.value:.2f})" if b.upset_ratio else ""
    print(f"  Сортамент .................... круг {b.diameter.value:g} x "
          f"{b.length.value:.0f} мм{ratio}")
    print(f"  Коэффициент уковки ........... {b.forge_ratio.value:.2f}")
    print(f"  КИМ .......................... {b.material_utilization.value:.0f} %")

    print("\nОБОРУДОВАНИЕ")
    print(f"  Площадь проекции ............. {q(fr.projection_area)}")
    if fr.force_required:
        print(f"  Требуемое усилие ............. {q(fr.force_required)}")
    if fr.falling_mass_required:
        print(f"  Требуемая масса п.ч. ......... {q(fr.falling_mass_required)}")
    if fr.main_equipment:
        e = fr.main_equipment
        print(f"  Основное ..................... {e.name_ru} — {e.value:g} {e.unit}")
        print(f"      {e.reason}")
    if fr.trim_equipment:
        e = fr.trim_equipment
        print(f"  Обрезное ..................... {e.name_ru} — {e.value:g} {e.unit}")

    if res.warnings:
        print("\nПРЕДУПРЕЖДЕНИЯ")
        for w in res.warnings:
            print(f"  ! {w}")

    print(f"\nЗначений без нормативного источника: {len(res.non_standard_values)}")
    print()


def main() -> int:
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]

    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    parts = [Part.from_dict(p) for p in data["parts"]]
    if args:
        parts = [parts[int(args[0]) - 1]]

    for part in parts:
        if as_json:
            print(json.dumps(pipeline.calculate(part).to_dict(), ensure_ascii=False, indent=2))
        else:
            report(part)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
