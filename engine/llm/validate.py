from __future__ import annotations

import json
from .. import paths
from typing import Any

from ..data import loader
from ..models import CalcResult
from . import baseline

SCHEMA_PATH = paths.instructions() / "output_schema.json"

REQUIRED_ALWAYS = {"005_cut", "010_heat", "060_clean", "070_control", "075_mark"}
REQUIRED_FLASH = {"030_finish", "045_trim"}
REQUIRED_HAMMER = {"025_preform"}
FLASH_PROCESSES = {"die_forging_hammer", "die_forging_press"}

ORDER_RULES = [
    ("005_cut", "010_heat", "резка заготовки идёт до нагрева"),
    ("010_heat", "015_upset", "нагрев идёт до осадки"),
    ("010_heat", "020_draw", "нагрев идёт до протяжки"),
    ("010_heat", "025_preform", "нагрев идёт до предварительной штамповки"),
    ("010_heat", "030_finish", "нагрев идёт до окончательной штамповки"),
    ("025_preform", "030_finish", "предварительная штамповка идёт до окончательной"),
    ("030_finish", "045_trim", "обрезка облоя идёт после окончательной штамповки"),
    ("045_trim", "050_straighten", "правка идёт после обрезки облоя"),
    ("030_finish", "055_heat_treat", "термообработка идёт после формообразования"),
    ("055_heat_treat", "060_clean", "очистка идёт после термообработки"),
    ("060_clean", "070_control", "контроль идёт после очистки"),
    ("070_control", "075_mark", "маркирование — последняя операция"),
]


def _schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def normalize(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    schema = _schema()
    allowed = set(schema["properties"])
    clean = {k: v for k, v in data.items() if k in allowed}

    item_fields = set(schema["properties"]["operations"]["items"]["properties"])
    ops = clean.get("operations")
    if isinstance(ops, list):
        clean["operations"] = [
            {k: v for k, v in op.items() if k in item_fields} if isinstance(op, dict) else op
            for op in ops
        ]
    return clean


def check_structure(data: Any) -> list[str]:
    errors: list[str] = []
    schema = _schema()

    if not isinstance(data, dict):
        return ["Ответ должен быть JSON-объектом"]

    for field in schema["required"]:
        if field not in data:
            errors.append(f"Отсутствует обязательное поле '{field}'")

    process = data.get("process")
    allowed = schema["properties"]["process"]["enum"]
    if process is not None and process not in allowed:
        errors.append(f"Недопустимый код техпроцесса '{process}'. Допустимы: {', '.join(allowed)}")

    ops = data.get("operations")
    if not isinstance(ops, list):
        errors.append("Поле 'operations' должно быть массивом")
        return errors
    if not ops:
        errors.append("Маршрут пуст: нужна хотя бы одна операция")
        return errors

    item_schema = schema["properties"]["operations"]["items"]
    for i, op in enumerate(ops):
        where = f"операция №{i + 1}"
        if not isinstance(op, dict):
            errors.append(f"{where}: должна быть объектом")
            continue
        for field in item_schema["required"]:
            if field not in op:
                errors.append(f"{where}: отсутствует поле '{field}'")
        num = op.get("number")
        if isinstance(num, str) and not (len(num) == 3 and num.isdigit()):
            errors.append(f"{where}: номер '{num}' должен быть трёхзначным, например 005")

    return errors


def required_codes(calc: CalcResult) -> set[str]:
    required = set(REQUIRED_ALWAYS)
    if calc.process in FLASH_PROCESSES:
        required |= REQUIRED_FLASH
    if calc.process == "die_forging_hammer":
        required |= REQUIRED_HAMMER
    return required


def check_semantics(data: dict[str, Any], calc: CalcResult) -> list[str]:
    errors: list[str] = []
    ops = data.get("operations") or []
    if not isinstance(ops, list) or not ops:
        return errors

    known = loader.operation_codes()
    by_code = {op["code"]: op for op in loader.operations()}
    codes = [op.get("code") for op in ops if isinstance(op, dict)]

    if data.get("process") != calc.process:
        errors.append(
            f"Техпроцесс изменён на '{data.get('process')}', а расчётом определён "
            f"'{calc.process}'. Скопируй код процесса из расчёта без изменений"
        )

    for code in codes:
        if code not in known:
            errors.append(
                f"Операции с кодом '{code}' нет в справочнике. "
                f"Используй только коды из списка допустимых операций"
            )

    for code in codes:
        op = by_code.get(code)
        if op and calc.process not in op["processes"]:
            errors.append(
                f"Операция '{code}' ({op['name_ru']}) неприменима к процессу '{calc.process}'"
            )

    for item in ops:
        if not isinstance(item, dict):
            continue
        code, eq = item.get("code"), item.get("equipment", "")
        op = by_code.get(code)
        if not op:
            continue
        allowed = op["equipment"]
        if not allowed:
            continue
        if eq not in allowed:
            errors.append(
                f"Операция '{code}': оборудование '{eq}' недопустимо. "
                f"Разрешено: {', '.join(allowed)}"
            )

    for item in ops:
        if not isinstance(item, dict):
            continue
        op = by_code.get(item.get("code"))
        allowed = [op["name_ru"], *op.get("name_variants", [])] if op else []
        if op and item.get("name") not in allowed:
            errors.append(
                f"Операция '{item.get('code')}': наименование '{item.get('name')}' "
                f"не совпадает со справочником — должно быть '{op['name_ru']}'"
            )

    for item in ops:
        if not isinstance(item, dict) or item.get("code") != "060_clean":
            continue
        equipment = item.get("equipment", "")
        want = baseline.clean_equipment(calc)
        if equipment != want:
            part_type = calc.part.part_type if calc.part else ""
            errors.append(
                f"Очистка: для поковки типа '{part_type}' оборудование должно быть "
                f"'{want}', а не '{equipment}'. Круглые в плане поковки очищают "
                f"в галтовочном барабане, остальные в дробемётной камере"
            )
        proper = baseline.operation_name("060_clean", equipment)
        if item.get("name") != proper:
            errors.append(
                f"Очистка: при оборудовании '{equipment}' операция называется "
                f"'{proper}', а не '{item.get('name')}'"
            )

    for code in sorted(required_codes(calc) - set(codes)):
        op = by_code.get(code)
        errors.append(
            f"В маршруте нет обязательной операции '{code}'"
            + (f" ({op['name_ru']})" if op else "")
        )

    has_hole = bool(calc.blank.mass_web and calc.blank.mass_web.value > 0)
    if "035_pierce" in codes and not has_hole:
        errors.append("Операция прошивки лишняя: в расчёте нет прошиваемого отверстия (масса выдры равна нулю)")
    if "035_pierce" in codes and calc.process in FLASH_PROCESSES:
        errors.append(
            "Отдельная прошивка при штамповке не нужна: внутренний диаметр штампуется "
            "в окончательном ручье, перемычка удаляется в обрезном штампе"
        )
    if has_hole and calc.process in FLASH_PROCESSES and "045_trim" not in codes:
        errors.append("В расчёте есть масса выдры — перемычку удаляют в обрезной операции '045_trim'")
    if has_hole and calc.process == "free_forging" and "035_pierce" not in codes:
        errors.append("В расчёте есть масса выдры — при свободной ковке нужна прошивка '035_pierce'")

    numbers = [op.get("number") for op in ops if isinstance(op, dict)]
    if len(set(numbers)) != len(numbers):
        errors.append("Номера операций повторяются — нумерация должна быть сквозной")
    try:
        ints = [int(n) for n in numbers]
        if ints != sorted(ints):
            errors.append("Номера операций идут не по возрастанию")
        bad = [n for n in ints if n % 5 or n == 0]
        if bad:
            errors.append(f"Номера операций должны быть кратны пяти: {bad}")
    except (TypeError, ValueError):
        pass

    seen = set()
    for code in codes:
        if code in seen:
            errors.append(f"Операция '{code}' встречается в маршруте дважды")
        seen.add(code)

    pos = {code: i for i, code in enumerate(codes)}
    for earlier, later, why in ORDER_RULES:
        if earlier in pos and later in pos and pos[earlier] > pos[later]:
            errors.append(f"Нарушен порядок: {why}")

    return errors


def validate(data: Any, calc: CalcResult) -> list[str]:
    data = normalize(data)
    errors = check_structure(data)
    if errors:
        return errors
    return check_semantics(data, calc)


def format_errors(errors: list[str]) -> str:
    lines = "\n".join(f"{i + 1}. {e}" for i, e in enumerate(errors))
    return (
        "Твой предыдущий ответ не прошёл проверку. Найдены ошибки:\n\n"
        f"{lines}\n\n"
        "Исправь ровно эти ошибки и верни JSON целиком, без пояснений."
    )
