from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..data import loader
from ..models import CalcResult
from ..progress import Report
from . import baseline, validate
from .client import LLMCancelled, LLMError, STOPPED_TEXT
from .generate import RouteResult, parse_response

DECISIONS = [
    {
        "code": "015_upset",
        "question": "Нужна ли в маршруте операция осадки?",
        "hint": "Осадку применяют для круглых в плане поковок (шестерни, фланцы, кольца, диски), "
                "чтобы получить нужное соотношение диаметра к высоте. Для валов и рычагов она не нужна.",
    },
    {
        "code": "020_draw",
        "question": "Нужна ли в маршруте операция протяжки?",
        "hint": "Протяжка — основная формообразующая операция свободной ковки удлинённых поковок "
                "(валы, оси, рычаги). При штамповке в закреплённом штампе обычно не применяется.",
    },
    {
        "code": "025_preform",
        "question": "Нужна ли предварительная штамповка (заготовительный и предварительный ручьи)?",
        "hint": "Обязательна при молотовой штамповке в многоручьевом штампе. На КГШП нужна только "
                "для поковок сложной формы — степени сложности С3 или С4.",
    },
    {
        "code": "035_pierce",
        "question": "Нужна ли операция прошивки отверстия?",
        "hint": "Только если в поковке есть прошиваемое отверстие. Признак — масса выдры больше нуля.",
    },
    {
        "code": "050_straighten",
        "question": "Нужна ли правка поковки?",
        "hint": "Нужна длинным поковкам (валы, рычаги), которые коробятся при остывании.",
    },
    {
        "code": "065_calibrate",
        "question": "Нужна ли калибровка (чеканка)?",
        "hint": "Применяется только на КГШП и только при высоких классах точности Т1 или Т2.",
    },
]

DECISION_SYSTEM = """Ты — технолог кузнечно-штамповочного производства.
Тебе задают один вопрос об одной операции. Ответь строго JSON:

{"needed": true или false, "rationale": "одна фраза с опорой на данные"}

Никакого текста вне JSON. Обоснование — на русском, одна короткая фраза,
опирающаяся на конкретные данные из карточки детали."""

MAX_ATTEMPTS_PER_DECISION = 2


@dataclass
class Decision:

    code: str
    needed: bool
    rationale: str
    source: str = "llm"
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    raw_responses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "needed": self.needed,
            "rationale": self.rationale,
            "source": self.source,
            "attempts": self.attempts,
            "errors": self.errors,
            "raw_responses": self.raw_responses,
        }


def digest(calc: CalcResult) -> str:
    part = calc.part
    assert part is not None
    f, b = calc.forging, calc.blank
    eq = calc.force.main_equipment

    has_hole = bool(b.mass_web and b.mass_web.value > 0)
    lines = [
        f"Деталь: {part.name}, тип {part.part_type}, сталь {part.material_grade}",
        f"Программа: {part.annual_program} шт./год",
        f"Техпроцесс: {calc.process}",
        f"Масса поковки: {f.mass_actual.value:.2f} кг" if f.mass_actual else "",
        f"Припуски: {f.norm}, тип поковки: {f.forging_kind}" if f.forging_kind else
        f"Степень сложности: {f.complexity}, класс точности: {f.accuracy_class}",
        f"Отверстие: {'есть, прошивается (масса выдры %.2f кг)' % b.mass_web.value if has_hole else 'нет'}",
        f"Заготовка: круг {b.diameter.value:g} мм"
        + (f", L/D = {b.upset_ratio.value:.2f}" if b.upset_ratio else "")
        if b.diameter else "",
        f"Оборудование: {eq.name_ru}, {eq.value:g} {eq.unit}" if eq else "",
        *(f"Заготовительный ручей — {baseline.lower_first(line)}" for line in baseline.preform_lines(calc)),
    ]
    return "\n".join(line for line in lines if line)


def _applicable(code: str, process: str) -> bool:
    for op in loader.operations():
        if op["code"] == code:
            return process in op["processes"]
    return False


TRUTHY = {"true", "да", "yes", "нужна", "нужно", "1", "y", "требуется"}
FALSHY = {"false", "нет", "no", "не нужна", "не нужно", "0", "n", "не требуется"}


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in TRUTHY:
            return True
        if text in FALSHY:
            return False
    return None


def _validate_decision(data: Any) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["Ответ должен быть JSON-объектом вида {\"needed\": true, \"rationale\": \"...\"}"], {}

    if "needed" not in data and len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict) and "needed" in inner:
            data = inner

    needed = _as_bool(data.get("needed"))
    if needed is None:
        errors.append(
            f"Поле 'needed' должно быть true или false, а пришло {data.get('needed')!r}"
        )

    rationale = data.get("rationale") or data.get("reason") or ""
    if not isinstance(rationale, str) or len(rationale.strip()) < 10:
        errors.append("Поле 'rationale' — одна осмысленная фраза на русском, не короче 10 знаков")
        rationale = str(rationale or "")
    elif len(rationale) > 300:
        errors.append("Поле 'rationale' слишком длинное: нужна одна фраза")

    return errors, {"needed": needed, "rationale": rationale.strip()}


def _rule_errors(code: str, needed: bool, calc: CalcResult) -> list[str]:
    if not needed and code in validate.required_codes(calc):
        return [f"операция обязательна при этом техпроцессе — {baseline._needed(code, calc)[1]}"]
    if code == "035_pierce":
        has_hole = bool(calc.blank.mass_web and calc.blank.mass_web.value > 0)
        if needed and not has_hole:
            return ["прошивать нечего: в расчёте нет отверстия, масса выдры равна нулю"]
        if has_hole and not needed:
            return [f"в расчёте есть прошиваемое отверстие, масса выдры {calc.blank.mass_web.value:.2f} кг"]
    return []


def ask_decision(spec: dict[str, str], calc: CalcResult, client: Any) -> Decision:
    code = spec["code"]
    messages = [
        {"role": "system", "content": DECISION_SYSTEM},
        {"role": "user", "content": (
            f"{digest(calc)}\n\n"
            f"ВОПРОС: {spec['question']}\n"
            f"СПРАВКА: {spec['hint']}\n\n"
            f"Ответ — только JSON."
        )},
    ]

    errors: list[str] = []
    raw: list[str] = []
    stopped = False
    for attempt in range(1, MAX_ATTEMPTS_PER_DECISION + 1):
        try:
            text = client.chat(messages)
        except LLMCancelled as exc:
            errors.append(str(exc))
            stopped = True
            break
        except LLMError as exc:
            errors.append(str(exc))
            break

        raw.append(text)
        data, parse_error = parse_response(text)
        if parse_error:
            found, clean = [parse_error], {}
        else:
            found, clean = _validate_decision(data)
            if not found:
                found = _rule_errors(code, clean["needed"], calc)
        if not found:
            return Decision(
                code=code,
                needed=bool(clean["needed"]),
                rationale=clean["rationale"],
                source="llm",
                attempts=attempt,
                raw_responses=raw,
            )

        errors.extend(found)
        if attempt < MAX_ATTEMPTS_PER_DECISION:
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    "Ответ не принят: " + "; ".join(found)
                    + '. Верни только JSON вида {"needed": true, "rationale": "..."}'
                )},
            ]

    needed, rationale = baseline._needed(code, calc)
    fallback = ("Построение остановлено — решение принято по правилам" if stopped
                else "Решение принято по правилам: модель не дала корректный ответ")
    return Decision(
        code=code,
        needed=needed,
        rationale=rationale or fallback,
        source="baseline",
        attempts=len(raw) if stopped else MAX_ATTEMPTS_PER_DECISION,
        errors=errors,
        raw_responses=raw,
    )


def _op_name(code: str) -> str:
    for op in loader.operations():
        if op["code"] == code:
            return str(op.get("name_ru") or code).lower()
    return code


def build(calc: CalcResult, client: Any = None,
          report: Optional[Report] = None) -> RouteResult:
    if client is None:
        route = baseline.build(calc)
        return RouteResult(route=route, source="baseline",
                           fallback_reason="Клиент модели не задан")

    asked = {spec["code"]: spec for spec in DECISIONS if _applicable(spec["code"], calc.process)}
    decisions: dict[str, Decision] = {}
    for number, (code, spec) in enumerate(asked.items(), 1):
        if report:
            report(f"решение: {_op_name(code)}", number, len(asked))
        decisions[code] = ask_decision(spec, calc, client)

    operations: list[dict[str, Any]] = []
    number = 0
    for op in loader.operations():
        code = op["code"]
        if calc.process not in op["processes"]:
            continue

        if code in decisions:
            decision = decisions[code]
            if not decision.needed:
                continue
            rationale = decision.rationale
        else:
            needed, rationale = baseline._needed(code, calc)
            if not needed:
                continue

        number += 5
        equipment = baseline._equipment_for(code, calc)
        operations.append({
            "number": f"{number:03d}",
            "code": code,
            "name": baseline.operation_name(code, equipment, calc),
            "equipment": equipment,
            "rationale": rationale,
        })

    by_model = [d.code for d in decisions.values() if d.source == "llm"]
    route = {
        "process": calc.process,
        "operations": operations,
        "summary": (
            f"{calc.part.name if calc.part else 'Деталь'}: {len(operations)} операций. "
            f"Решений принято моделью: {len(by_model)} из {len(asked)}. "
            f"{calc.process_reason}"
        ),
    }

    result = RouteResult(
        route=route,
        source="stepwise",
        attempts=len(asked),
        decisions=[d.to_dict() for d in decisions.values()],
    )

    errors = validate.validate(route, calc)
    if errors:
        result.route = baseline.build(calc)
        result.source = "baseline"
        result.fallback_reason = "Маршрут, собранный по частям, не прошёл проверку: " + "; ".join(errors)
        result.errors.append(errors)

    failed = [d for d in decisions.values() if d.source == "baseline"]
    stopped = [d for d in failed if STOPPED_TEXT in d.errors]
    if stopped:
        result.fallback_reason = (
            (result.fallback_reason + " ") if result.fallback_reason else ""
        ) + (f"{STOPPED_TEXT}: моделью принято {len(by_model)} из {len(asked)} решений, "
             f"остальные — по правилам ({', '.join(d.code for d in stopped)})")
        failed = [d for d in failed if d not in stopped]
        if not by_model:
            result.source = "baseline"
    if failed:
        result.fallback_reason = (
            (result.fallback_reason + " ") if result.fallback_reason else ""
        ) + f"По правилам решены операции: {', '.join(d.code for d in failed)}"

    return result


def decisions_table(result: RouteResult, show_raw: bool = False) -> str:
    rows = result.decisions
    if not rows:
        return "Решений нет: маршрут собран не по частям"

    lines = [f"  {'операция':<16}{'решение':<10}{'кто':<12}попыток  обоснование"]
    failed = []
    for d in rows:
        mark = "нужна" if d["needed"] else "не нужна"
        lines.append(
            f"  {d['code']:<16}{mark:<10}{d['source']:<12}{d['attempts']:<9}{d['rationale'][:60]}"
        )
        if d.get("errors"):
            failed.append(d)

    if failed:
        lines.append("")
        lines.append("  Почему модель не приняли:")
        for d in failed:
            lines.append(f"    {d['code']}:")
            for err in d["errors"]:
                lines.append(f"      - {err}")
            if show_raw:
                for i, text in enumerate(d.get("raw_responses", []), 1):
                    short = text.strip().replace("\n", " ")[:300]
                    lines.append(f"      ответ {i}: {short}")
    return "\n".join(lines)
