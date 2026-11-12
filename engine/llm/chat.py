from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..models import CalcResult
from . import numbers, prompt
from .client import LLMCancelled, LLMError
from .generate import parse_response

MODES = ("explain", "consult", "edit", "whatif")
MODE_NAMES = {"explain": "Объяснить", "consult": "Консультация", "edit": "Правка маршрута",
              "whatif": "Что если"}

WHATIF_FIELDS = {
    "material_grade": "Марка стали",
    "annual_program": "Программа выпуска, шт./год",
    "heating": "Нагрев",
    "roughness_ra_um": "Шероховатость Ra, мкм",
    "machined_all_over": "Обработка по всем поверхностям",
}
WHATIF_DIMS = {"d": "диаметр", "d_inner": "внутренний диаметр", "l": "длина",
               "a": "длина", "b": "ширина", "c": "высота"}
HEATERS = {"flame": "пламенная печь", "induction": "индукционный нагрев"}

HISTORY_TURNS = 6
MAX_SOURCES_LINES = 70
MAX_ANSWER = 1500
MAX_QUESTION = 800

SYSTEM = """Ты — инженер-технолог кузнечно-штамповочного производства, помощник в программе FORGE.
Отвечаешь по-русски, коротко и по делу: 2–6 предложений, без вступлений и без повторения вопроса.
Говоришь как технолог в цехе: «поковка», «облой», «ручей», «перемычка», «уковка».
Ответ — только JSON: {"answer": "<текст ответа>", "sources": ["<источник>", ...]}."""

TASK = {
    "explain": """РЕЖИМ: ОБЪЯСНИТЬ РАСЧЁТ.
Отвечай только по данным ниже: блоки РАСЧЁТ, ЗНАЧЕНИЯ И ИСТОЧНИКИ, РАЗМЕРЫ ПОКОВКИ, МАРШРУТ.
Правила, которые проверяет программа:
- Любое число в ответе должно быть из этих блоков. Своих чисел не придумывай.
- Если для ответа нужного числа или нормы в данных нет, так и скажи: «в расчёте этого нет».
- Объясняя значение, называй его источник из списка (стандарт, таблица, пункт) — дословно.
- В "sources" перечисли источники из списка, на которые опирается ответ. Нет таких — пустой список.
- Помеченные «не ГОСТ» значения так и называй: они из справочной литературы, а не из стандарта.""",
    "consult": """РЕЖИМ: КОНСУЛЬТАЦИЯ.
Общий вопрос о технологии ковки и штамповки. Можно отвечать по общим знаниям.
Если вопрос касается текущей детали — опирайся на данные ниже.
Если не уверен — так и скажи. Числа, которых нет в данных, называй ориентировочными
и советуй сверить по справочнику. В "sources" укажи, на что опираешься
(стандарт, справочник), или оставь пустым.""",
    "edit": """РЕЖИМ: ПРАВКА МАРШРУТА.
Человек просит изменить маршрут: убрать или добавить операцию. Ты предлагаешь правку,
программа применяет её к копии маршрута, проверяет по справочнику и показывает человеку —
в маршрут она попадает, только если он нажмёт «Принять».
Правила:
- Убирать и добавлять можно только операции по их кодам. Добавлять — только коды
  из списка ДОПУСТИМЫЕ ОПЕРАЦИИ. Порядок и номера программа расставит сама.
- Оборудование назначает программа по расчёту, его не указывай.
- Если просьба нарушает технологию (например, убрать обрезку облоя у облойной
  штамповки), не предлагай правку, а объясни почему — оставь remove и add пустыми.
- Обоснование добавленной операции — одна фраза, числа только из расчёта.
Ответ — только JSON:
{"answer": "<что и почему меняешь>", "remove": ["<код>", ...],
 "add": [{"code": "<код>", "rationale": "<обоснование>"}], "sources": []}""",
    "whatif": """РЕЖИМ: ЧТО ЕСЛИ.
Человек спрашивает, что будет, если изменить исходные данные детали. Ты переводишь вопрос
в изменение исходных данных; пересчитывает программа, а не ты — результатов не угадывай.
Менять можно только:
- "material_grade" — марку стали, строго из списка МАРКИ;
- "annual_program" — программу выпуска, целое число шт./год;
- "heating" — нагрев: "flame" (пламенная печь) или "induction" (индукционный);
- "roughness_ra_um" — шероховатость Ra, мкм;
- "machined_all_over" — обработка по всем поверхностям, true или false;
- размеры элементов геометрии в "primitives": по имени элемента из блока ДЕТАЛЬ,
  поля d, d_inner, l, a, b, c в мм.
Если вопрос нельзя выразить такими изменениями — оставь changes и primitives пустыми
и объясни в answer.
Ответ — только JSON:
{"answer": "<что меняешь>", "changes": {"<поле>": <значение>},
 "primitives": [{"name": "<элемент>", "<размер>": <число>}], "sources": []}""",
}

REFERENCE_RE = re.compile(
    r"(?<!\w)(ГОСТ\s*(?:Р\s*)?[\d.]+(?:[-–]\d+)?"
    r"|(?:табл(?:ица|ице|ицы|ицу)?|прил(?:ожение|ожения|ожении)?|рис(?:унок|унке)?"
    r"|пример(?:а|е)?|п(?:ункт(?:а|е)?)?|графа|графе|графы|черт(?:ёж|еж)?|форм[аеуы]?)"
    r"\.?\s*№?\s*\d+(?:[.,]\d+)*[а-я]?)",
    re.IGNORECASE)


@dataclass
class ChatAnswer:
    mode: str
    answer: str = ""
    sources: list[str] = field(default_factory=list)
    verified: bool = False
    invented: list[float] = field(default_factory=list)
    attempts: int = 0
    error: str = ""
    stopped: bool = False
    seconds: float = 0.0
    raw: list[str] = field(default_factory=list)
    proposal: Optional[dict[str, Any]] = None
    rejected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "mode_name": MODE_NAMES.get(self.mode, self.mode),
            "answer": self.answer,
            "sources": self.sources,
            "verified": self.verified,
            "invented": self.invented,
            "attempts": self.attempts,
            "error": self.error,
            "stopped": self.stopped,
            "seconds": round(self.seconds, 1),
            "proposal": self.proposal,
            "rejected": self.rejected,
        }


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def render_sources(calc_dict: dict[str, Any], limit: int = MAX_SOURCES_LINES) -> str:
    lines: list[str] = []

    def walk(node: Any, path: str) -> None:
        if len(lines) >= limit:
            return
        if isinstance(node, dict):
            if "value" in node and "source" in node and isinstance(node.get("value"), (int, float)):
                src = str(node.get("source") or "—")[:110]
                mark = "" if node.get("standard", True) else " (не ГОСТ)"
                lines.append(f"  {path}: {_fmt(node['value'])} {node.get('unit', '')}".rstrip()
                             + f" — {src}{mark}")
                return
            for k, v in node.items():
                if k in ("part", "dimensions", "primitives", "warnings", "non_standard_values",
                         "non_standard", "normative"):
                    continue
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node[:12]):
                walk(v, f"{path}[{i}]")

    walk(calc_dict, "")
    return "\n".join(lines)


def render_dimensions(calc_dict: dict[str, Any]) -> str:
    rows = (calc_dict.get("forging") or {}).get("dimensions") or []
    out = []
    for r in rows:
        tol = r.get("tolerance") or {}
        up, low = (r.get("upper") or {}).get("value"), (r.get("lower") or {}).get("value")
        out.append(
            f"  {r.get('label')}: деталь {_fmt(r.get('part_mm'))}, припуск {_fmt(r.get('allowance_mm'))}, "
            f"поковка {_fmt(r.get('forging_mm'))}, допуск {_fmt(tol.get('value'))} "
            f"(+{_fmt(up)} / {_fmt(low)}) — {str(tol.get('source') or '')[:90]}")
    return "\n".join(out)


def render_route(route: Optional[dict[str, Any]], transitions: Optional[list] = None,
                 equipment_names: Optional[dict[str, str]] = None) -> str:
    if not route or not route.get("operations"):
        return "  маршрут не построен"
    names = equipment_names or {}
    by_code = {t.get("code"): t for t in (transitions or [])}
    out = []
    for op in route["operations"]:
        eq = names.get(op.get("equipment") or "", op.get("equipment") or "—")
        out.append(f"  {op.get('number')} {op.get('name')} — {eq}. {op.get('rationale', '')}")
        for t in (by_code.get(op.get("code")) or {}).get("transitions", [])[:8]:
            out.append(f"      {t.get('number')}. {t.get('text')}")
    return "\n".join(out)


def build_messages(mode: str, question: str, calc: Optional[CalcResult],
                   route: Optional[dict[str, Any]] = None, transitions: Optional[list] = None,
                   history: Optional[list[dict[str, str]]] = None,
                   equipment_names: Optional[dict[str, str]] = None) -> list[dict[str, str]]:
    context = []
    if calc is not None:
        d = calc.to_dict()
        context += [
            f"РАСЧЁТ\n{prompt.render_calc(calc)}",
            f"ЗНАЧЕНИЯ И ИСТОЧНИКИ\n{render_sources(d)}",
            f"РАЗМЕРЫ ПОКОВКИ, мм\n{render_dimensions(d)}",
            f"МАРШРУТ\n{render_route(route, transitions, equipment_names)}",
        ]
    else:
        context.append("ДЕТАЛЬ НЕ ЗАДАНА — расчёта нет.")

    if mode == "edit" and calc is not None:
        context.append(prompt.render_allowed_operations(calc.process))
    if mode == "whatif" and calc is not None and calc.part is not None:
        from ..data import loader
        part = calc.part
        prims = "\n".join(
            f"  {p.name or p.kind}: " + ", ".join(f"{k}={_fmt(getattr(p, k))}" for k in WHATIF_DIMS
                                                    if getattr(p, k, 0))
            + (" (вычитается)" if p.subtract else "")
            for p in part.primitives)
        context.append(
            "ДЕТАЛЬ (исходные данные)\n"
            f"  material_grade: {part.material_grade}\n  annual_program: {part.annual_program}\n"
            f"  heating: {part.heating}\n  roughness_ra_um: {part.roughness_ra_um}\n"
            f"  machined_all_over: {str(part.machined_all_over).lower()}\n"
            f"  primitives:\n{prims}")
        context.append("МАРКИ: " + ", ".join(m["grade"] for m in loader.load("materials.json")["materials"]))
    messages = [{"role": "system", "content": SYSTEM + "\n\n" + TASK[mode] + "\n\n" + "\n\n".join(context)}]
    for turn in (history or [])[-HISTORY_TURNS:]:
        role = "assistant" if turn.get("role") == "ai" else "user"
        text = str(turn.get("text") or "").strip()
        if text:
            messages.append({"role": role, "content": text[:MAX_ANSWER]})
    messages.append({"role": "user", "content": question.strip()[:MAX_QUESTION]})
    return messages


def allowed_numbers(calc: Optional[CalcResult], route: Optional[dict[str, Any]],
                    question: str) -> set[float]:
    allowed: set[float] = set()
    if calc is not None:
        numbers.collect_allowed(calc.to_dict(), allowed)
    if route:
        numbers.collect_allowed(route, allowed)
        for op in route.get("operations") or []:
            allowed.update(numbers.extract(str(op.get("number") or "")))
            allowed.update(numbers.extract(str(op.get("rationale") or "")))
    allowed.update(numbers.extract(question))
    return allowed


def invented_numbers(answer: str, allowed: set[float]) -> list[float]:
    plain = REFERENCE_RE.sub(" ", answer)
    return numbers.check(plain, allowed)


def _parse(text: str) -> tuple[Optional[str], list[str], Optional[str]]:
    data, error = parse_response(text)
    if error:
        return None, [], error
    answer = data.get("answer") if isinstance(data, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        return None, [], 'Нужен JSON вида {"answer": "...", "sources": [...]} с непустым answer'
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        sources = [str(sources)]
    return answer.strip()[:MAX_ANSWER], [str(s).strip()[:120] for s in sources if str(s).strip()][:6], None


def ask(mode: str, question: str, client: Any, calc: Optional[CalcResult] = None,
        route: Optional[dict[str, Any]] = None, transitions: Optional[list] = None,
        history: Optional[list[dict[str, str]]] = None,
        equipment_names: Optional[dict[str, str]] = None,
        part_raw: Optional[dict[str, Any]] = None,
        part_parser: Optional[Callable[[dict[str, Any]], Any]] = None) -> ChatAnswer:
    if mode not in MODES:
        raise ValueError(f"Неизвестный режим чата: {mode}")
    result = ChatAnswer(mode=mode)
    started = time.monotonic()
    messages = build_messages(mode, question, calc, route, transitions, history, equipment_names)
    allowed = allowed_numbers(calc, route, question)
    attempts = 2

    for attempt in range(1, attempts + 1):
        result.attempts = attempt
        try:
            text = client.chat(messages)
        except LLMCancelled as exc:
            result.stopped, result.error = True, str(exc)
            break
        except LLMError as exc:
            result.error = str(exc)
            break
        result.raw.append(text)
        answer, sources, error = _parse(text)
        if error:
            result.error = error
            messages = messages + [{"role": "assistant", "content": text},
                                   {"role": "user", "content": error}]
            continue
        result.error = ""
        result.answer, result.sources = answer, sources
        result.invented = invented_numbers(answer, allowed)
        result.verified = not result.invented
        if mode == "edit":
            data, _ = parse_response(text)
            proposal, problems = propose_edit(data or {}, calc, route, transitions, allowed)
            result.proposal, result.rejected = proposal, problems
            if problems and attempt < attempts:
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        "Правка не прошла проверку маршрута:\n" + "\n".join(f"- {e}" for e in problems)
                        + "\nИсправь правку или, если просьба нарушает технологию, оставь remove и add "
                          "пустыми и объясни это в answer. Верни JSON целиком.")},
                ]
                continue
            break
        if mode == "whatif":
            data, _ = parse_response(text)
            proposal, problems = propose_whatif(data or {}, part_raw, part_parser)
            result.proposal, result.rejected = proposal, problems
            if problems and attempt < attempts:
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        "Изменение не принято:\n" + "\n".join(f"- {e}" for e in problems)
                        + "\nИсправь и верни JSON целиком.")},
                ]
                continue
            break
        if mode == "explain" and result.invented and attempt < attempts:
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    "Ответ не принят: " + numbers.format_error(result.invented)
                    .replace("в переходе", "в ответе").replace("блока РАСЧЁТ", "данных выше")
                    + '. Если нужного числа в данных нет — напиши «в расчёте этого нет». '
                      'Верни JSON {"answer": "...", "sources": [...]}')},
            ]
            continue
        break

    if result.answer and mode == "consult":
        result.verified = False
    result.seconds = time.monotonic() - started
    return result


def apply_edit(route: dict[str, Any], calc: CalcResult, remove: list[str],
               add: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from ..data import loader
    from . import baseline
    order = [op["code"] for op in loader.operations()]
    current = {op["code"]: dict(op) for op in route.get("operations") or []}
    changes: list[dict[str, Any]] = []
    for code in remove:
        if code in current:
            changes.append({"action": "remove", "code": code, "name": current[code].get("name"),
                            "number": current[code].get("number")})
            del current[code]
    for item in add:
        code = item.get("code")
        if code in current:
            continue
        equipment = baseline._equipment_for(code, calc)
        rationale = str(item.get("rationale") or "").strip() or baseline._needed(code, calc)[1] \
            or "Добавлено по просьбе технолога"
        current[code] = {"code": code, "name": baseline.operation_name(code, equipment),
                         "equipment": equipment, "rationale": rationale[:300]}
        changes.append({"action": "add", "code": code, "name": current[code]["name"]})
    ops = sorted(current.values(), key=lambda op: order.index(op["code"]) if op["code"] in order else 999)
    for i, op in enumerate(ops, 1):
        op["number"] = f"{i * 5:03d}"
    for ch in changes:
        if ch["action"] == "add":
            ch["number"] = current[ch["code"]]["number"]
    new_route = {**route, "operations": ops}
    return new_route, changes


def propose_edit(data: dict[str, Any], calc: Optional[CalcResult], route: Optional[dict[str, Any]],
                 transitions: Optional[list], allowed: set[float]) -> tuple[Optional[dict[str, Any]], list[str]]:
    from . import transitions as transitions_mod
    from . import validate
    if calc is None or not route or not route.get("operations"):
        return None, []
    remove = [str(c).strip() for c in (data.get("remove") or []) if str(c).strip()]
    add = [a if isinstance(a, dict) else {"code": str(a)} for a in (data.get("add") or [])]
    add = [a for a in add if str(a.get("code") or "").strip()]
    if not remove and not add:
        return None, []
    problems: list[str] = []
    codes_now = {op["code"] for op in route["operations"]}
    for code in remove:
        if code not in codes_now:
            problems.append(f"Операции '{code}' в маршруте нет — убирать нечего")
    for a in add:
        invented = invented_numbers(str(a.get("rationale") or ""), allowed)
        if invented:
            problems.append(numbers.format_error(invented, f"обоснование {a.get('code')}")
                            .replace("в переходе", "в обосновании"))
    new_route, changes = apply_edit(route, calc, remove, add)
    problems += validate.validate(new_route, calc)
    if not changes:
        problems.append("Правка ничего не меняет в маршруте")
    if problems:
        return None, problems
    new_transitions = None
    if transitions:
        by_code = {t.get("code"): t for t in transitions}
        new_transitions = []
        for op in new_route["operations"]:
            row = by_code.get(op["code"])
            if row is None:
                row = {"code": op["code"], "name": op["name"], "source": "baseline", "attempts": 0,
                       "errors": [], "transitions": transitions_mod.baseline_transitions(op["code"], calc)}
            new_transitions.append(row)
    return {"kind": "edit", "changes": changes, "route": new_route, "transitions": new_transitions}, []


def _show(field_name: str, value: Any) -> str:
    if field_name == "heating":
        return HEATERS.get(value, str(value))
    if field_name == "machined_all_over":
        return "да" if value else "нет"
    return _fmt(value)


def propose_whatif(data: dict[str, Any], part_raw: Optional[dict[str, Any]],
                   part_parser: Optional[Callable[[dict[str, Any]], Any]]
                   ) -> tuple[Optional[dict[str, Any]], list[str]]:
    import copy
    if not part_raw:
        return None, []
    changes_in = data.get("changes") or {}
    prims_in = data.get("primitives") or []
    if not isinstance(changes_in, dict):
        return None, ["changes должен быть объектом {поле: значение}"]
    if not isinstance(prims_in, list):
        return None, ["primitives должен быть списком"]
    if not changes_in and not prims_in:
        return None, []
    new = copy.deepcopy(part_raw)
    problems: list[str] = []
    changes: list[dict[str, Any]] = []
    for key, value in changes_in.items():
        if key not in WHATIF_FIELDS:
            problems.append(f"Поле '{key}' менять нельзя. Можно: {', '.join(WHATIF_FIELDS)}")
            continue
        if key == "annual_program":
            try:
                value = int(float(value))
            except (TypeError, ValueError):
                problems.append("annual_program должно быть целым числом")
                continue
        elif key == "roughness_ra_um":
            try:
                value = float(value)
            except (TypeError, ValueError):
                problems.append("roughness_ra_um должно быть числом")
                continue
        elif key == "machined_all_over":
            if isinstance(value, str):
                value = value.strip().lower() in ("true", "да", "1")
            value = bool(value)
        elif key == "heating" and value not in HEATERS:
            problems.append("heating: только flame или induction")
            continue
        old = new.get(key)
        if old == value:
            continue
        new[key] = value
        changes.append({"field": key, "label": WHATIF_FIELDS[key], "old": _show(key, old),
                        "new": _show(key, value)})
    by_name = {str(p.get("name") or "").strip().lower(): p for p in new.get("primitives") or []}
    for item in prims_in:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        target = by_name.get(name.lower())
        if target is None:
            problems.append(f"Элемента «{name}» в детали нет. Есть: "
                            + ", ".join(p.get("name") or p.get("kind") or "?"
                                        for p in new.get("primitives") or []))
            continue
        for key, value in item.items():
            if key == "name":
                continue
            if key not in WHATIF_DIMS:
                problems.append(f"«{name}»: размер '{key}' менять нельзя")
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                problems.append(f"«{name}»: размер '{key}' не число")
                continue
            old = target.get(key)
            if old == value:
                continue
            target[key] = value
            changes.append({"field": f"{name}.{key}", "label": f"{name}: {WHATIF_DIMS[key]}, мм",
                            "old": _fmt(old), "new": _fmt(value)})
    if not problems and not changes:
        problems.append("Изменение совпадает с текущими данными — пересчитывать нечего")
    if not problems and part_parser is not None:
        try:
            part_parser(new)
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(f"Деталь с такими данными не считается: {exc}")
    if problems:
        return None, problems
    return {"kind": "whatif", "part": new, "changes": changes}, []


def compare_variants(old: CalcResult, new: CalcResult, ops_old: int, ops_new: int) -> list[dict[str, Any]]:
    from .. import names

    def eq(c: CalcResult) -> str:
        e = c.force.main_equipment
        return f"{e.name_ru} {e.value:g} {e.unit}" if e else "—"

    def q(x: Any) -> str:
        if x is None:
            return "—"
        s = f"{x.value:.2f}"
        return s.rstrip("0").rstrip(".")

    def rows(c: CalcResult, ops: int) -> list[tuple[str, str]]:
        f, b, h = c.forging, c.blank, c.heating
        return [
            ("Процесс", names.process_name(c.process)),
            ("Оборудование", eq(c)),
            ("Припуски по", f.norm),
            ("Группа стали · сложность · класс",
             " · ".join(names.gost_code(x) for x in (f.steel_group, f.complexity, f.accuracy_class) if x) or "—"),
            ("Исходный индекс", str(f.initial_index) if f.initial_index else "—"),
            ("Масса поковки, кг", q(f.mass_actual)),
            ("Заготовка, мм", f"⌀{b.diameter.value:g}×{b.length.value:.0f}" if b.diameter and b.length else "—"),
            ("Масса заготовки, кг", q(b.mass_blank)),
            ("КИМ", q(b.consumption_ratio)),
            ("Интервал ковки, °C",
             f"{h.temp_start.value:.0f}–{h.temp_end.value:.0f}" if h.temp_start and h.temp_end else "—"),
            ("Операций (по правилам)", str(ops)),
        ]

    return [{"label": label, "old": a, "new": b, "changed": a != b}
            for (label, a), (_, b) in zip(rows(old, ops_old), rows(new, ops_new))]
