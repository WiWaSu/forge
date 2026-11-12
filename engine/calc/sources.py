from __future__ import annotations

import re
from typing import Any, Optional

from ..data import loader

GROUPS = ("Поковка", "Заготовка", "Нагрев", "Оборудование")

NAMES: dict[str, tuple[str, str, str]] = {
    "forging.kr": ("Поковка", "Коэффициент Кр для оценки массы поковки",
                   "по типу детали из таблицы 20 стандарта"),
    "forging.mass_actual": ("Поковка", "Масса поковки",
                            "по номинальным размерам поковки"),
    "blank.mass_flash": ("Заготовка", "Облой — металл, который уходит в заусенец по разъёму штампа",
                         "доля от массы поковки по степени сложности"),
    "blank.mass_web": ("Заготовка", "Выдра — перемычка, которую выбивают из отверстия",
                       "объём перемычки, умноженный на плотность стали"),
    "blank.mass_scale": ("Заготовка", "Угар — металл, который сгорает в окалину при нагреве",
                         "доля от массы металла при выбранном способе нагрева"),
    "blank.mass_cut_loss": ("Заготовка", "Потери при резке прутка на заготовки",
                            "доля от массы заготовки"),
    "blank.mass_blank": ("Заготовка", "Масса заготовки",
                         "поковка + облой + выдра + угар + потери при резке"),
    "blank.volume_blank": ("Заготовка", "Объём заготовки",
                           "масса заготовки, делённая на плотность стали"),
    "blank.diameter": ("Заготовка", "Диаметр прутка",
                       "ближайший подходящий диаметр из сортамента"),
    "blank.length": ("Заготовка", "Длина заготовки",
                     "объём заготовки, делённый на площадь сечения прутка"),
    "blank.upset_ratio": ("Заготовка", "Отношение длины заготовки к диаметру",
                          "длина, делённая на диаметр"),
    "blank.forge_ratio": ("Заготовка", "Коэффициент уковки — насколько изменилось сечение металла",
                          "отношение сечений заготовки и поковки"),
    "blank.material_utilization": ("Заготовка", "Какая доля металла заготовки уходит в деталь",
                                   "масса детали, делённая на массу заготовки"),
    "blank.consumption_ratio": ("Заготовка", "Расход металла на 1 кг детали (КИМ по заводскому счёту)",
                                "масса заготовки, делённая на массу детали"),
    "heating.temp_start": ("Нагрев", "Температура начала ковки", "по марке стали"),
    "heating.temp_end": ("Нагрев", "Температура конца ковки", "по марке стали"),
    "heating.scale_percent": ("Нагрев", "Угар при нагреве, доля от массы металла",
                              "по способу нагрева"),
    "heating.hold_time": ("Нагрев", "Выдержка в печи перед ковкой",
                          "1 минута на 1 мм диаметра заготовки"),
    "heating.treat_hold_time": ("Нагрев", "Выдержка в печи при термообработке",
                                "2 минуты на 1 мм наибольшей толщины поковки"),
    "heating.treat_temp_min": ("Нагрев", "Температура термообработки поковки, от",
                               "по марке стали"),
    "heating.treat_temp_max": ("Нагрев", "Температура термообработки поковки, до",
                               "по марке стали"),
    "force.projection_area": ("Оборудование", "Площадь поковки в плане",
                              "по размерам поковки; нужна для подбора оборудования"),
    "force.force_required": ("Оборудование", "Требуемое усилие штамповки",
                             "по площади поковки в плане и сопротивлению стали деформации"),
    "force.falling_mass_required": ("Оборудование", "Требуемая масса падающих частей молота",
                                    "по площади поковки в плане"),
    "force.trim_force_required": ("Оборудование", "Требуемое усилие обрезки облоя",
                                  "по усилию штамповки"),
}

KINDS = {
    "approx": ("сверить", "Приближённая формула — значение оценочное, сверить по справочнику"),
    "expert": ("указание технолога", "Норма со слов практикующего технолога"),
    "factory": ("заводская норма", "Так считают на заводе; стандарт эту величину не задаёт"),
    "book": ("справочник", "Взято из справочной литературы"),
    "calc": ("расчёт", "Посчитано программой из величин выше"),
}
KIND_ORDER = list(KINDS)

DIM_NAMES = {"tolerance": "Допуск", "upper": "Верхнее отклонение", "lower": "Нижнее отклонение"}


def _num(value: float, digits: int) -> str:
    grouped = abs(value) >= 10000
    text = (f"{value:,.{digits}f}" if grouped else f"{value:.{digits}f}").replace(",", " ").replace(".", ",")
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text.replace(" ", " ")


def value_text(value: float, unit: str) -> str:
    v = float(value)
    if unit == "кг":
        return f"{_num(v, 3 if abs(v) < 1 else 2 if abs(v) < 100 else 0)} кг"
    if unit == "мм3":
        return f"{_num(v / 1000.0, 1)} см³"
    if unit == "мм2":
        return f"{_num(v / 100.0, 1)} см²"
    if unit == "мм":
        return f"{_num(v, 1)} мм"
    if unit == "МН":
        return f"{_num(v * 1000.0, 1)} кН" if abs(v) < 1 else f"{_num(v, 2)} МН"
    if unit == "%":
        return f"{_num(v, 1)} %"
    if unit in ("мин", "°C"):
        return f"{_num(v, 0)} {unit}"
    if unit:
        return f"{_num(v, 2)} {unit}"
    return _num(v, 2)


def _book(text: str) -> str:
    text = text.strip().rstrip(".")
    m = re.match(r"^([А-ЯЁ][а-яё]+ [А-ЯЁ]\.\s?[А-ЯЁ]\.)\s+(.+)$", text)
    if m:
        return f"{m.group(1)} «{m.group(2)}»"
    m = re.match(r"^Ковка и штамповка: справочник(.*)$", text)
    if m:
        rest = m.group(1).strip(" ,")
        rest = re.sub(r"^в 4 т\.\s*", "", rest)
        rest = rest.replace(", т.2 Горячая объёмная штамповка", ", т. 2 «Горячая объёмная штамповка»")
        return "Справочник «Ковка и штамповка»" + (f" {rest}" if rest else "")
    return text


def _books(text: str) -> list[str]:
    return [_book(part) for part in text.split(";") if part.strip()]


def describe_source(path: str, raw: str, calc: dict[str, Any]) -> tuple[str, str, list[str]]:
    src = (raw or "").strip()
    grade = ((calc.get("material") or {}).get("grade") or "").strip()
    grade_gost = ((calc.get("material") or {}).get("gost") or "").strip()

    if "ПРИБЛИЖЁННАЯ" in src or "приближение для свободной ковки" in src:
        if "свободной ковки" in src:
            return "approx", ("Таблицы ГОСТ 7505-89 для штамповки, применённые к свободной ковке: "
                              "для неё нужен ГОСТ 7829-70"), []
        return "approx", "Размер вне таблиц ГОСТ 7505-89 — значение по приближённой формуле", []
    if "(требует сверки)" in src:
        book = src.replace("(требует сверки)", "").strip()
        book = book.split(":")[0] if path == "force.falling_mass_required" else book
        return "approx", "Приближённая формула", _books(book)
    if src.startswith("Указание практикующего технолога"):
        date = re.search(r"\d{2}\.\d{2}\.\d{4}", src)
        return "expert", ((f"от {date.group(0)}; " if date else "")
                          + "в ГОСТ 7505-89 не нормируется"), []
    if src.startswith("Заводская норма"):
        return "factory", "в ГОСТ 7505-89 не нормируется", []
    if src.endswith("losses.json"):
        return "book", "", _books(loader.source_of("losses.json"))
    if "температурные интервалы ковки" in src:
        book = src.split("температурные интервалы ковки —", 1)[1].split(";")[0].strip()
        book = re.sub(r"Марочник сталей и сплавов под ред\. Сорокина В\.Г\.",
                      "«Марочник сталей и сплавов» под ред. В.Г. Сорокина", book)
        note = f"Для стали {grade}" if grade else ""
        if grade_gost:
            note += f" (марка — по {grade_gost})" if note else f"Марка — по {grade_gost}"
        return "book", note, [book]
    if src.startswith("ГОСТ 2590"):
        how = src.split(";", 1)[1].strip() if ";" in src else ""
        how = how.split(":", 1)[1].strip() if ":" in how else how
        text = "Диаметр — из сортамента ГОСТ 2590-2006; правило подбора не из стандарта"
        return "calc", text + (f" ({how})" if how else ""), []
    if src.startswith("Условие устойчивости"):
        upset = "осадк" in str(((calc.get("blank") or {}).get("diameter") or {}).get("source") or "")
        if upset:
            return "book", ("Правило устойчивости при осадке: длина не больше 2,5 диаметра, "
                            "иначе пруток изогнётся"), []
        return "calc", "предел 2,5 действует только при осадке, здесь её нет", []
    if src in ("Расчёт", "Сумма составляющих", "Расчёт по размерам поковки"):
        return "calc", "", []
    if src.startswith("Ковка и штамповка") or re.match(r"^[А-ЯЁ][а-яё]+ [А-ЯЁ]\.", src):
        return "book", "", _books(src)
    return "book", src or "—", []


def _name_for(path: str, calc: dict[str, Any]) -> Optional[tuple[str, str, str]]:
    key = re.sub(r"\[\d+\]", "[]", path)
    if key in NAMES:
        return NAMES[key]
    m = re.match(r"^forging\.dimensions\[(\d+)\]\.(tolerance|upper|lower)$", path)
    if m:
        rows = (calc.get("forging") or {}).get("dimensions") or []
        i = int(m.group(1))
        label = rows[i].get("label") if i < len(rows) else ""
        return ("Поковка", f"{DIM_NAMES[m.group(2)]}: {label or 'размер поковки'}",
                "по таблице допусков")
    if re.match(r"^blank\.material_items\[\d+\]\.mass$", path):
        return None
    m = re.match(r"^blank\.material_items\[(\d+)\]\.percent$", path)
    if m:
        items = (calc.get("blank") or {}).get("material_items") or []
        i = int(m.group(1))
        name = items[i].get("name") if i < len(items) else "статья"
        return ("Заготовка", f"Доля «{name}» в норме расхода", "масса статьи, делённая на норму расхода")
    return ("Прочее", path, "")


def walk(data: Any, path: str, out: list[tuple[str, dict[str, Any]]]) -> None:
    if isinstance(data, dict):
        if "value" in data and "standard" in data and "unit" in data:
            if not data["standard"]:
                out.append((path, data))
            return
        for key, value in data.items():
            if key in ("non_standard", "non_standard_values", "normative"):
                continue
            walk(value, f"{path}.{key}" if path else key, out)
    elif isinstance(data, list):
        for i, value in enumerate(data):
            walk(value, f"{path}[{i}]", out)


def non_standard(calc: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[tuple[str, dict[str, Any]]] = []
    walk(calc, "", found)
    rows: list[dict[str, Any]] = []
    for path, q in found:
        named = _name_for(path, calc)
        if named is None:
            continue
        group, name, how = named
        kind, where, books = describe_source(path, str(q.get("source") or ""), calc)
        rows.append({
            "path": path,
            "group": group,
            "name": name,
            "value_text": value_text(q.get("value") or 0, str(q.get("unit") or "")),
            "how": how,
            "source_text": where,
            "books": books,
            "kind": kind,
            "kind_name": KINDS[kind][0],
        })
    order = {g: i for i, g in enumerate(GROUPS)}
    rows.sort(key=lambda r: (order.get(r["group"], len(GROUPS)), KIND_ORDER.index(r["kind"])))
    return rows


def normative(calc: dict[str, Any]) -> list[dict[str, str]]:
    f = calc.get("forging") or {}
    norm = f.get("norm") or "ГОСТ 7505-89"
    items: list[dict[str, str]] = []
    if norm.startswith("ГОСТ 7505"):
        what = ["припуски на механическую обработку", "допуски и предельные отклонения размеров"]
        if f.get("accuracy_class"):
            what.append("класс точности")
        if f.get("steel_group"):
            what.append("группа стали")
        if f.get("complexity"):
            what.append("степень сложности")
        if f.get("initial_index"):
            what.append("исходный индекс")
        what.append("масса поковки — по номинальным размерам (п. 2.6)")
        items.append({"standard": "ГОСТ 7505-89", "title": "Поковки стальные штампованные",
                      "what": ", ".join(what)})
    else:
        items.append({"standard": norm, "title": "Поковки из углеродистой и легированной стали, "
                      "изготовляемые ковкой на молотах", "what": "припуски, напуски и допуски на размеры поковки"})
    material = calc.get("material") or {}
    if material.get("gost"):
        items.append({"standard": material["gost"], "title": "Марка стали",
                      "what": f"сталь {material.get('grade', '')}".strip()})
    stock = calc.get("stock") or {}
    if stock.get("gost"):
        items.append({"standard": stock["gost"], "title": "Сортамент проката",
                      "what": f"{(stock.get('profile') or 'круг').lower()} — ряд диаметров прутка"})
    items.append({"standard": "ГОСТ 3.1403-85", "title": "Карта технологического процесса ковки и штамповки",
                  "what": "коэффициент использования поковки и статьи расхода металла (графы 32–35)"})
    items.append({"standard": "ГОСТ 3.1118-82", "title": "Маршрутная карта",
                  "what": "форма маршрутной карты"})
    return items
