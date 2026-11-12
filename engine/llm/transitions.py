from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import names
from ..data import loader
from ..models import CalcResult
from ..progress import Report
from . import baseline, numbers, prompt
from .client import LLMError
from .generate import parse_response


MAX_ATTEMPTS = 2
MIN_TRANSITIONS = 2
MAX_TRANSITIONS = 8
MAX_TEXT = 150
MIN_TEXT = 8


@dataclass
class OperationTransitions:

    code: str
    name: str
    transitions: list[dict[str, Any]]
    source: str = "llm"
    attempts: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "transitions": self.transitions,
            "source": self.source,
            "attempts": self.attempts,
            "errors": self.errors,
        }


def baseline_transitions(code: str, calc: CalcResult) -> list[dict[str, Any]]:
    template: list[str] = []
    for op in loader.operations():
        if op["code"] == code:
            template = list(op.get("transitions", []))
            break

    b, h, f = calc.blank, calc.heating, calc.forging
    eq = calc.force.main_equipment

    if code == "005_cut" and b.diameter and b.length:
        template = [
            f"Установить заготовку ⌀{b.diameter.value:g} мм на пилу, "
            f"отрезать в размер длиной {b.length.value:.0f} мм",
            "Контролировать длину заготовки",
        ]
    elif code == "010_heat" and h.temp_start:
        template = [
            "Загрузить заготовку в нагреватель",
            f"Нагреть заготовку до {h.temp_start.value:.0f} °C",
        ]
        if h.hold_time:
            to_what = "штамповочной" if calc.process != "free_forging" else "ковочной"
            template.append(
                f"Выдержать заготовку в печи до {to_what} температуры "
                f"{h.hold_time.value:.0f} минут"
            )
        template.append(
            f"Температурный интервал деформации {h.temp_start.value:.0f} — {h.temp_end.value:.0f} °C"
        )
    elif code == "025_preform" and b.preform:
        draw = baseline.preform_text(calc, "протяжка")
        roll = baseline.preform_text(calc, "подкатка")
        template = [f"Установить заготовку в {'протяжной' if draw else 'подкатной'} ручей штампа"]
        if draw:
            template.append(f"Протянуть участки {draw}")
        if roll:
            template.append(f"{'Переложить заготовку в подкатной ручей, п' if draw else 'П'}"
                            f"одкатать с набором металла на участок {roll}")
        if f.complexity in ("C3", "C4"):
            template.append("Переложить в предварительный ручей, штамповать предварительно")
    elif code == "030_finish" and eq:
        template = [
            "Установить заготовку в окончательный ручей штампа",
            "Штамповать окончательно",
        ]
        if b.mass_web and b.mass_web.value > 0:
            template.append("Отштамповать внутренний диаметр")
        template.append("Извлечь поковку из ручья")
    elif code == "045_trim" and b.mass_flash:
        template = [
            "Установить поковку на матрицу обрезного штампа",
            f"Обрубить облой массой {b.mass_flash.value:.2f} кг",
        ]
        if b.mass_web and b.mass_web.value > 0:
            template.append(
                f"Прошить отверстие, удалить перемычку массой {b.mass_web.value:.2f} кг"
            )
    elif code == "070_control":
        template = ["Контролировать размеры поковки по чертежу поковки"]
        if f.mass_actual:
            template.append(f"Контролировать массу поковки {f.mass_actual.value:.2f} кг")
        template.append("Контролировать отсутствие поверхностных дефектов")
    elif code == "060_clean":
        tumbling = (calc.part.part_type in baseline.TUMBLED_PART_TYPES) if calc.part else False
        where = "галтовочный барабан" if tumbling else "дробемётную камеру"
        work = "галтовку" if tumbling else "дробемётную обработку"
        template = [
            f"Установить поковку в {where}",
            f"Произвести {work}, удалить окалину с поверхности поковки",
        ]
    elif code == "055_heat_treat":
        template = ["Загрузить поковку в печь"]
        if h.treat_temp_min and h.treat_temp_max:
            template.append(f"Нагреть поковку до {h.treat_temp_min.value:g}–{h.treat_temp_max.value:g} °C")
        if h.treat_hold_time:
            template.append(f"Выдержать поковку в печи {h.treat_hold_time.value:.0f} минут")
        else:
            template.append("Выдержать поковку в печи")
        template.append("Охладить поковку с печью" if h.treat_cooling == "с печью" else "Охладить на воздухе")
        if "отпуск" in h.treat_kind:
            template.append("Провести высокий отпуск поковки")

    return [{"number": i, "text": t} for i, t in enumerate(template, 1)]


EQUIPMENT_WORDS = ("станк", "молот", "пресс", "печ", "ножниц", "пил", "автомат",
                   "индукцион", "камер", "барабан", "гкм", "вальц",
                   "дробемет", "галтов")

MACHINE_NAME_WORDS = ("кгшп", "гкм", "паровоздушн", "горячештамповочн", "кривошипн",
                      "гидравлическ", "пневматическ", "горизонтально-ковочн",
                      "падающих част")
SPEC_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(мн|кн|тс|кг)(?![а-я])")
FORCE_UNITS = ("мн", "кн", "тс")

REMOVE_WORDS = ("удал", "сня", "сним", "срез", "обрез", "отрез", "отдел", "убра", "отруб")
FORMING_CODES = ("015_upset", "025_preform", "030_finish")

ROUND_ONLY_RE = re.compile(r"диаметр\w*(?: и \w+)? поковк")

MINUTES_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*мин")
ROUGHNESS_RE = re.compile(r"шероховат|\bra\s*\d")

DESIGN_WORDS = ("индекс", "класс точност", "сложност", "группа стали", "группу стали", "группы стали")

ELEMENT_WORDS = ("обод", "ступиц", "диск", "полотн", "бобышк", "шейк", "буртик", "фланц")

WORDING = {
    "улож": "«установить»",
    "пробить": "«прошить» или «удалить»",
    "поддерживать температур": "«температурный интервал деформации»",
    "отжигать": "«выдержка поковки в печи»",
    "в воздухе": "«на воздухе»",
    "обрезать облой": "«обрубить облой»",
    "отрезать облой": "«обрубить облой»",
    "разрезать": "«отрезать»",
    "равнотемператур": "«до ковочной (штамповочной) температуры»",
    "оставить в печи": "«выдержать поковку в печи»",
    "остудить": "«охладить»",
}

WORDING_IN_OPERATION = {
    "005_cut": {"перевернуть": "мерную заготовку режут за один рез",
                "вторую сторону": "мерную заготовку режут за один рез",
                "первую сторону": "мерную заготовку режут за один рез"},
    "055_heat_treat": {**{w: "в термической операции температуру ковки не пишут — "
                             "у термообработки свой режим"
                          for w in ("начала ковк", "конца ковк", "температура ковк",
                                    "температуры ковк", "температуре ковк", "ковочн")}},
    "030_finish": {"прошить отверстие": "в ручье пишут «отштамповать внутренний диаметр»",
                   "наметить отверстие": "в ручье пишут «отштамповать внутренний диаметр»"},
    "025_preform": {"прошить отверстие": "в ручье пишут «отштамповать внутренний диаметр»"},
}

CONTEXT_WORDS = {
    "ручей": ("025_preform", "030_finish", "015_upset", "035_pierce", "040_bend", "065_calibrate"),
    "штамп": ("015_upset", "025_preform", "030_finish", "035_pierce", "040_bend", "045_trim",
              "050_straighten", "065_calibrate"),
    "обло": ("025_preform", "030_finish", "045_trim", "070_control"),
    "заусен": ("030_finish", "045_trim", "070_control"),
    "перемычк": ("035_pierce", "045_trim", "070_control"),
    "выдр": ("035_pierce", "045_trim", "070_control"),
    "обруб": ("045_trim",),
    "нагре": ("010_heat", "055_heat_treat"),
    "закалк": ("055_heat_treat",),
    "отпуск": ("055_heat_treat",),
    "нормализац": ("055_heat_treat",),
    "окалин": ("060_clean", "010_heat", "055_heat_treat"),
}


HOURS = {"час", "часа", "часов", "часу", "ч"}

AFTER_FORMING = ("045_trim", "050_straighten", "055_heat_treat", "060_clean",
                 "065_calibrate", "070_control", "075_mark")

COUNT_WORDS = ("один", "одного", "одним", "два", "двух", "три", "трех", "четыр",
               "пят", "шест", "сем", "восем", "девят", "десят", "несколько")


def _counts_blows(low: str) -> bool:
    words = low.replace(",", " ").split()
    for i, word in enumerate(words):
        if not word.startswith("удар"):
            continue
        before = words[max(0, i - 2):i]
        if any(w.isdigit() or w.startswith(COUNT_WORDS) for w in before):
            return True
    return False


def _whole(low: str, stem: str) -> str:
    i = low.index(stem) + len(stem)
    while i < len(low) and low[i].isalpha():
        i += 1
    return low[low.index(stem):i]


def facts_of(calc: CalcResult) -> dict[str, Any]:
    b, h, rows = calc.blank, calc.heating, calc.forging.dimensions
    return {
        "blank": tuple(v for v in (b.diameter.value if b.diameter else 0,
                                   round(b.length.value) if b.length else 0) if v),
        "forging": tuple(r["forging_mm"] for r in rows) + tuple(r["part_mm"] for r in rows),
        "flash": round(b.mass_flash.value, 2) if b.mass_flash else 0.0,
        "scale": round(b.mass_scale.value, 2) if b.mass_scale else 0.0,
        "temp_start": h.temp_start.value if h.temp_start else 0.0,
        "temp_end": h.temp_end.value if h.temp_end else 0.0,
        "upset_ratio": round(b.upset_ratio.value, 2) if b.upset_ratio else 0.0,
        "part_mass": round(calc.forging.mass_part.value, 2) if calc.forging.mass_part else 0.0,
        "forging_mass": round(calc.forging.mass_actual.value, 2) if calc.forging.mass_actual else 0.0,
        "hold": round(h.hold_time.value) if h.hold_time else 0.0,
        "web": round(b.mass_web.value, 2) if b.mass_web else 0.0,
        "treat_kind": h.treat_kind,
        "treat_temp": ((h.treat_temp_min.value, h.treat_temp_max.value)
                       if h.treat_temp_min and h.treat_temp_max else None),
        "treat_hold": round(h.treat_hold_time.value) if h.treat_hold_time else 0.0,
    }


def _near(value: float, target: float) -> bool:
    return abs(value - target) <= max(0.06, abs(target) * 0.005)


def _fact_errors(low: str, where: str, code: str, facts: dict[str, Any]) -> list[str]:
    values = numbers.extract(low)
    minutes = [float(m.replace(",", ".")) for m in MINUTES_RE.findall(low)]
    errors: list[str] = []
    hold, treat = facts.get("hold") or 0.0, facts.get("treat_hold") or 0.0
    if code == "055_heat_treat" and hold and any(_near(m, hold) and not _near(m, treat) for m in minutes):
        errors.append(f"{where}: {hold:g} мин — выдержка заготовки перед ковкой; при термообработке — {treat:g} мин")
    if code == "010_heat" and treat and any(_near(m, treat) and not _near(m, hold) for m in minutes):
        errors.append(f"{where}: {treat:g} мин — выдержка при термообработке; перед ковкой — {hold:g} мин")
    if code in AFTER_FORMING:
        blank = [v for v in numbers.extract(MINUTES_RE.sub(" ", low)) if any(_near(v, x) for x in facts["blank"])
                 and not any(_near(v, x) for x in facts["forging"])]
        if blank:
            errors.append(f"{where}: {', '.join(f'{v:g}' for v in blank)} мм — размеры заготовки; "
                          f"у поковки свои, они в разделе «Размеры поковки»")
    if facts["flash"] and "окалин" in low and any(_near(v, facts["flash"]) for v in values):
        errors.append(f"{where}: {facts['flash']:g} кг — масса облоя, а угар (окалина) — {facts['scale']:g} кг")
    if (code in FORMING_CODES and facts["temp_end"] and any(_near(v, facts["temp_end"]) for v in values)
            and not any(_near(v, facts["temp_start"]) for v in values)):
        errors.append(f"{where}: {facts['temp_end']:g} °C — конец интервала ковки, при нём не штампуют")
    if (code in AFTER_FORMING and facts.get("part_mass") and any(_near(v, facts["part_mass"]) for v in values)
            and not any(_near(v, facts["forging_mass"]) for v in values)):
        errors.append(f"{where}: {facts['part_mass']:g} кг — масса детали; у поковки {facts['forging_mass']:g} кг")
    if code == "015_upset" and facts["upset_ratio"] and any(_near(v, facts["upset_ratio"]) for v in values):
        errors.append(f"{where}: L/D {facts['upset_ratio']:g} — заготовка до осадки, а не результат осадки")
    return errors


def _normalize(text: str) -> str:
    return " ".join(str(text).lower().replace("ё", "е").split())


def sense_errors(items: list[dict], code: str, equipment_ru: str,
                 elements: tuple[str, ...] = (), round_part: bool = True,
                 equipment_sizes: tuple[tuple[float, str], ...] = (),
                 facts: Optional[dict[str, Any]] = None) -> list[str]:
    errors: list[str] = []
    assigned = _normalize(equipment_ru)
    known = _normalize(" ".join(elements))
    seen: dict[str, int] = {}

    for i, item in enumerate(items, 1):
        text = item.get("text")
        if not isinstance(text, str):
            continue
        low = _normalize(text)
        where = f"переход {i}"

        same = seen.get(low)
        if same:
            errors.append(f"{where}: повторяет переход {same} дословно")
        else:
            seen[low] = i

        for stem in EQUIPMENT_WORDS:
            if stem in low and stem not in assigned:
                named = next((w for w in low.split() if stem in w), stem)
                errors.append(
                    f"{where}: названо оборудование «{named}», которого нет в операции "
                    f"({equipment_ru or 'оборудование не требуется'})"
                )
                break

        for stem in MACHINE_NAME_WORDS:
            if stem in low:
                named = next((w for w in low.split() if stem in w), stem)
                errors.append(f"{where}: оборудование («{named}») в переходе не называют — "
                              f"оно указано в графе «Оборудование»")
                break

        for found in SPEC_RE.finditer(low):
            value, unit = float(found.group(1).replace(",", ".")), found.group(2)
            if unit in FORCE_UNITS or any(abs(value - size) < 1e-6 and unit == u.lower()
                                          for size, u in equipment_sizes):
                errors.append(f"{where}: характеристики оборудования («{found.group(0)}») "
                              f"в переходе не пишут — они указаны в графе «Оборудование»")
                break

        if code in FORMING_CODES and "обло" in low and any(w in low for w in REMOVE_WORDS):
            errors.append(f"{where}: в ручье облой только образуется — удаляют его "
                          f"в обрезке облоя, отдельной операцией")

        if not round_part and ROUND_ONLY_RE.search(low):
            errors.append(f"{where}: поковка некруглая — «диаметр поковки» не контролируют; "
                          f"размеры называй по чертежу: {', '.join(elements) or 'элементы детали'}")

        plain = low.replace("штамповочной температур", "")
        for word, allowed_codes in CONTEXT_WORDS.items():
            if word in plain and code not in allowed_codes:
                errors.append(f"{where}: слово «{word}» не к месту в операции {code}")

        for stem in ELEMENT_WORDS:
            if stem in low and stem not in known:
                named = next((w for w in low.split() if stem in w), stem)
                errors.append(
                    f"{where}: в детали нет такой части — «{named}»; "
                    f"элементы детали: {', '.join(elements) or 'не заданы'}"
                )
                break

        for stem in DESIGN_WORDS:
            if stem in low:
                errors.append(f"{where}: «{_whole(low, stem)}» — расчётная характеристика для припусков, "
                              f"в переходах её не пишут; контролируют размеры и массу поковки")
                break

        if ROUGHNESS_RE.search(low):
            errors.append(f"{where}: шероховатость Ra — требование к обработанной детали, "
                          f"у поковки её не контролируют")

        if code in AFTER_FORMING and "масс" in low and "детал" in low:
            errors.append(f"{where}: детали после штамповки ещё нет — масса здесь у поковки")

        if HOURS & set(low.replace(",", " ").split()):
            errors.append(f"{where}: время выдержки указывают в минутах, а не в часах")

        if code in AFTER_FORMING and "заготовк" in low:
            errors.append(f"{where}: после штамповки это уже поковка, а не заготовка")

        if "удар" in low:
            if "пресс" in assigned:
                errors.append(f"{where}: пресс штампует за ход, а не за удары — "
                              f"«удар» бывает только у молота")
            elif _counts_blows(low):
                errors.append(f"{where}: число ударов система не рассчитывает — "
                              f"напиши без него")

        if code == "010_heat" and "индукцион" in assigned and "выдерж" in low:
            errors.append(f"{where}: при индукционном нагреве выдержки нет — "
                          f"заготовку греют насквозь в индукторе")

        for word, better in WORDING.items():
            if word in low:
                errors.append(f"{where}: так не говорят — «{_whole(low, word)}», правильно {better}")
        for word, why in WORDING_IN_OPERATION.get(code, {}).items():
            if word in low:
                errors.append(f"{where}: «{_whole(low, word)}» — {why}")

        if code == "055_heat_treat" and "°c" in low:
            errors.extend(_treat_temp_errors(low, where, facts or {}))

        if facts:
            errors.extend(_fact_errors(low, where, code, facts))

    if code == "055_heat_treat":
        errors.extend(_treat_order_errors([_normalize(i.get("text") or "") for i in items], facts or {}))
    if (code == "045_trim" and (facts or {}).get("web")
            and not any("перемычк" in _normalize(i.get("text") or "") or "выдр" in _normalize(i.get("text") or "")
                        for i in items)):
        errors.append(f"в поковке прошивается отверстие: перемычку массой {facts['web']:g} кг удаляют "
                      f"в этой операции — нужен переход «прошить отверстие, удалить перемычку»")
    return errors


def _treat_temp_errors(low: str, where: str, facts: dict[str, Any]) -> list[str]:
    span = facts.get("treat_temp")
    if not span:
        return [f"{where}: режим термообработки для этой стали не задан — температуру назначает технолог"]
    bad = [v for v in numbers.extract(low) if v >= 300 and not span[0] <= v <= span[1]]
    if bad:
        return [f"{where}: {', '.join(f'{v:g}' for v in bad)} °C вне режима: "
                f"{facts.get('treat_kind') or 'термообработка'} {span[0]:g}–{span[1]:g} °C"]
    return []


def _treat_order_errors(texts: list[str], facts: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cool = next((i for i, t in enumerate(texts) if "охлад" in t), None)
    hold = next((i for i, t in enumerate(texts) if "выдерж" in t or "нагре" in t), None)
    if cool is not None and hold is not None and cool < hold:
        errors.append(f"переход {cool + 1}: поковку охлаждают после нагрева и выдержки, а не до")
    if any("нормализ" in t for t in texts) and any("отжиг" in t or "отжечь" in t for t in texts):
        kind = facts.get("treat_kind")
        errors.append("в одной термической операции одна обработка, нормализация или отжиг"
                      + (f": для этой стали — {kind}" if kind else ""))
    return errors


def validate(data: Any, code: str, allowed: set[float], equipment_ru: str = "",
             elements: tuple[str, ...] = (), round_part: bool = True,
             equipment_sizes: tuple[tuple[float, str], ...] = (),
             facts: Optional[dict[str, Any]] = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["Ответ должен быть JSON-объектом"]

    if data.get("operation") not in (None, code):
        errors.append(f"Код операции изменён на '{data.get('operation')}', ожидался '{code}'")

    extra = set(data) - {"operation", "transitions"}
    if extra:
        errors.append(f"Лишние поля: {', '.join(sorted(extra))}")

    items = data.get("transitions")
    if not isinstance(items, list) or not items:
        return errors + ["Поле 'transitions' должно быть непустым массивом"]
    if len(items) < MIN_TRANSITIONS:
        errors.append(f"Переходов меньше {MIN_TRANSITIONS}: операция раскрыта не полностью")
    if len(items) > MAX_TRANSITIONS:
        errors.append(f"Переходов больше {MAX_TRANSITIONS}: дроби операцию, а не переход")

    for i, item in enumerate(items, 1):
        where = f"переход {i}"
        if not isinstance(item, dict):
            errors.append(f"{where}: должен быть объектом")
            continue
        unknown = set(item) - {"number", "text"}
        if unknown:
            errors.append(f"{where}: лишние поля {', '.join(sorted(unknown))}")
        if item.get("number") != i:
            errors.append(f"{where}: номер должен быть {i}, а не {item.get('number')!r}")
        text = item.get("text")
        if not isinstance(text, str) or len(text.strip()) < MIN_TEXT:
            errors.append(f"{where}: текст пустой или слишком короткий")
            continue
        if len(text) > MAX_TEXT:
            errors.append(f"{where}: текст длиннее {MAX_TEXT} знаков — нужна одна строка")
        invented = numbers.check(text, allowed)
        if invented:
            errors.append(numbers.format_error(invented, where))

    errors.extend(sense_errors([i for i in items if isinstance(i, dict)], code,
                               equipment_ru, elements, round_part, equipment_sizes, facts))
    return errors


def _capital(text: str) -> str:
    text = text.strip()
    return text[:1].upper() + text[1:]


def ask_operation(op: dict[str, Any], calc: CalcResult, client: Any,
                  allowed: set[float]) -> OperationTransitions:
    code, name = op["code"], op["name"]
    equipment_ru = names.equipment_name(op.get("equipment") or "")
    elements = tuple(p.name for p in (calc.part.primitives if calc.part else []) if p.name)
    round_part = not (calc.part and calc.part.envelope_kind == "box")
    sizes = tuple((e.value, e.unit) for e in (calc.force.main_equipment, calc.force.trim_equipment)
                  if e)
    task = prompt.instruction("operation_card")

    messages = [
        {"role": "system", "content": prompt.system_prompt()},
        {"role": "user", "content": (
            f"{task}\n\n"
            f"РАСЧЁТ\n{prompt.render_calc(calc, detailed=True)}\n\n"
            f"ОПЕРАЦИЯ, которую нужно раскрыть\n"
            f"  Код: {code}\n"
            f"  Наименование: {name}\n"
            f"  Оборудование: {equipment_ru or 'не требуется'}\n"
            f"  Место в маршруте: {op.get('rationale', '')}\n\n"
            f"Опиши переходы. Ответ — только JSON."
        )},
    ]

    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            text = client.chat(messages)
        except LLMError as exc:
            errors.append(str(exc))
            break

        data, parse_error = parse_response(text)
        found = ([parse_error] if parse_error
                 else validate(data, code, allowed, equipment_ru, elements, round_part, sizes, facts_of(calc)))
        if not found:
            return OperationTransitions(
                code=code, name=name, source="llm", attempts=attempt,
                transitions=[{"number": t["number"], "text": _capital(t["text"])}
                             for t in data["transitions"]],
            )

        errors.extend(found)
        if attempt < MAX_ATTEMPTS:
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    "Ответ не принят:\n" + "\n".join(f"- {e}" for e in found)
                    + "\nИсправь и верни JSON целиком."
                )},
            ]

    return OperationTransitions(
        code=code, name=name, source="baseline", attempts=MAX_ATTEMPTS,
        transitions=baseline_transitions(code, calc), errors=errors,
    )


def build(route: dict[str, Any], calc: CalcResult,
          client: Any = None,
          report: Optional[Report] = None) -> list[dict[str, Any]]:
    allowed = numbers.collect_allowed(calc.to_dict())
    result: list[dict[str, Any]] = []
    operations = route.get("operations", [])

    for number, op in enumerate(operations, 1):
        if report:
            report(f"переходы: {str(op.get('name') or op['code']).lower()}",
                   number, len(operations))
        if client is None:
            item = OperationTransitions(
                code=op["code"], name=op["name"], source="baseline",
                transitions=baseline_transitions(op["code"], calc),
            )
        else:
            item = ask_operation(op, calc, client, allowed)
        result.append(item.to_dict())

    return result
