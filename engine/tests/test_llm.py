from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["OLLAMA_HOST"] = "127.0.0.1:9"

from engine import names
from engine.api import server
from engine.calc import pipeline
from engine.llm import baseline, chat, generate, numbers, profiles, prompt, stepwise, transitions, validate
from engine.llm.client import STOPPED_TEXT, LLMCancelled, LLMError, OllamaClient, StoppableClient
from engine.llm import client as llm_client
from engine.models import Part
from engine.tests.mock import MockClient

SAMPLES = Path(__file__).resolve().parents[2] / "knowledge" / "examples" / "parts.json"
RAW = {p["name"]: p for p in json.loads(SAMPLES.read_text(encoding="utf-8"))["parts"]}
PARTS = {name: Part.from_dict(raw) for name, raw in RAW.items()}
GEAR_RAW = RAW["Шестерня (диск с отверстием)"]
GEAR = pipeline.calculate(PARTS["Шестерня (диск с отверстием)"])
ALLOWED = numbers.collect_allowed(GEAR.to_dict())
HAMMER = "Молот паровоздушный штамповочный"
PRESS = "Кривошипный горячештамповочный пресс (КГШП)"
TRIM = "Обрезной кривошипный пресс"


def every_calc():
    return [(name, pipeline.calculate(part)) for name, part in PARTS.items()]


def rule_answers(calc) -> list[dict]:
    answers = []
    for spec in stepwise.DECISIONS:
        if stepwise._applicable(spec["code"], calc.process):
            needed, rationale = baseline._needed(spec["code"], calc)
            answers.append({"needed": needed, "rationale": rationale or "по параметрам расчёта"})
    return answers


def two_transitions(route) -> list[dict]:
    return [{"operation": op["code"], "transitions": [
        {"number": 1, "text": "Установить на рабочее место"},
        {"number": 2, "text": "Удалить перемычку, снять деталь" if op["code"] == "045_trim"
         else "Выполнить операцию и снять деталь"}]} for op in route["operations"]]


def codes(route) -> list[str]:
    return [op["code"] for op in route["operations"]]


def clean_op(route) -> dict:
    return next(op for op in route["operations"] if op["code"] == "060_clean")


def chat_answer(text, sources=()):
    return json.dumps({"answer": text, "sources": list(sources)}, ensure_ascii=False)


def edit_answer(text, remove=(), add=()):
    return json.dumps({"answer": text, "remove": list(remove), "add": list(add), "sources": []}, ensure_ascii=False)


def whatif_answer(text, changes=None, prims=None):
    return json.dumps({"answer": text, "changes": changes or {}, "primitives": prims or [], "sources": []},
                      ensure_ascii=False)


class StopAfter:

    def __init__(self, answers, stop_after):
        self.mock = MockClient(responses=list(answers))
        self.stop_after = stop_after
        self.stopped = False

    def chat(self, messages):
        text = self.mock.chat(messages)
        self.stopped = len(self.mock.calls) >= self.stop_after
        return text


class Down:

    def chat(self, messages):
        raise LLMError("Ollama недоступна")


class Baseline(unittest.TestCase):

    def test_routes_pass_validation(self):
        for name, calc in every_calc():
            with self.subTest(name):
                route = baseline.build(calc)
                self.assertEqual(validate.validate(route, calc), [])
                for code in ("005_cut", "010_heat", "060_clean", "070_control"):
                    self.assertIn(code, codes(route))
                self.assertEqual(codes(route)[-1], "075_mark")

    def test_upset_only_for_round_parts(self):
        self.assertIn("015_upset", codes(baseline.build(GEAR)))
        self.assertNotIn("015_upset", codes(baseline.build(pipeline.calculate(PARTS["Вал ступенчатый"]))))

    def test_hole(self):
        for name, calc in every_calc():
            with self.subTest(name):
                route = codes(baseline.build(calc))
                has_hole = calc.blank.mass_web.value > 0
                if calc.process == "free_forging":
                    self.assertEqual("035_pierce" in route, has_hole)
                    continue
                self.assertNotIn("035_pierce", route)
                if has_hole:
                    self.assertIn("045_trim", route)
                    finish = " ".join(t["text"] for t in transitions.baseline_transitions("030_finish", calc))
                    self.assertIn("отштамповать внутренний диаметр", finish.lower())

    def test_cleaning_matches_part_type(self):
        for name, calc in every_calc():
            for route in (baseline.build(calc), stepwise.build(calc, MockClient(rule_answers(calc))).route):
                with self.subTest(name):
                    op = clean_op(route)
                    tumbling = op["equipment"] == "tumbling"
                    self.assertEqual(tumbling, calc.part.part_type in baseline.TUMBLED_PART_TYPES)
                    self.assertEqual("галтов" in op["name"].lower(), tumbling)
                    text = " ".join(t["text"] for r in transitions.build(route, calc, None)
                                    if r["code"] == "060_clean" for t in r["transitions"]).lower()
                    self.assertEqual("галтов" in text, tumbling)
                    self.assertNotEqual("дробе" in text, tumbling)

    def test_calc_for_model_names_forging_and_scale(self):
        calc = pipeline.calculate(PARTS["Вал ступенчатый"])
        text = prompt.render_calc(calc, detailed=True)
        self.assertNotIn("Размеры поковки", prompt.render_calc(calc))
        for row in calc.forging.dimensions:
            self.assertIn(f"{row['label']}: {row['part_mm']:g} → {row['forging_mm']:g}", text)
        self.assertIn(f"Масса угара (окалины): {calc.blank.mass_scale.value:.2f} кг", text)
        self.assertIn(f"Выдержка заготовки в печи: {calc.heating.hold_time.value:.0f} мин", text)
        flange = prompt.render_calc(pipeline.calculate(PARTS["Фланец"]))
        self.assertIn("Отношение L/D заготовки до осадки", flange)
        facts = transitions.facts_of(calc)
        self.assertEqual(facts["blank"], (calc.blank.diameter.value, round(calc.blank.length.value)))
        self.assertEqual((facts["temp_start"], facts["temp_end"]), (1200.0, 800.0))

    def test_stepped_shaft_preform(self):
        calc = pipeline.calculate(PARTS["Вал ступенчатый"])
        op = next(o for o in baseline.build(calc)["operations"] if o["code"] == "025_preform")
        self.assertIn("протяжка участков шейки левой до ⌀67.4 мм и шейки правой до ⌀57.4 мм из ⌀85 мм", op["rationale"])
        self.assertIn("подкатка с набором металла на участок средней ступени до ⌀88 мм", op["rationale"])
        texts = [t["text"] for t in transitions.baseline_transitions("025_preform", calc)]
        self.assertEqual(texts, [
            "Установить заготовку в протяжной ручей штампа",
            "Протянуть участки шейки левой до ⌀67.4 мм и шейки правой до ⌀57.4 мм",
            "Переложить заготовку в подкатной ручей, подкатать с набором металла на участок средней ступени до ⌀88 мм",
        ])
        self.assertIn("Подкатной: набор металла на участок средней ступени до ⌀88 мм", prompt.render_calc(calc))
        self.assertIn("протяжной: участки шейки левой до ⌀67.4 мм и шейки правой до ⌀57.4 мм, из ⌀85 мм",
                      stepwise.digest(calc))

    def test_control_checks_forging(self):
        for name, calc in every_calc():
            with self.subTest(name):
                op = next(o for o in baseline.build(calc)["operations"] if o["code"] == "070_control")
                texts = [op["rationale"]] + [t["text"] for t in transitions.baseline_transitions("070_control", calc)]
                mass = f"{calc.forging.mass_actual.value:.2f} кг"
                self.assertTrue(all("индекс" not in t for t in texts))
                self.assertIn(mass, op["rationale"])
                self.assertIn(f"Контролировать массу поковки {mass}", texts)

    def test_trim_names_web(self):
        for name, has_hole in (("Фланец", True), ("Вал ступенчатый", False)):
            with self.subTest(name):
                calc = pipeline.calculate(PARTS[name])
                route = baseline.build(calc)
                op = next(o for o in route["operations"] if o["code"] == "045_trim")
                self.assertEqual(op["name"] == "Обрезная (обрезка облоя и прошивка перемычки)", has_hole)
                self.assertEqual(f"перемычки {calc.blank.mass_web.value:.2f} кг" in op["rationale"], has_hole)
                self.assertEqual(validate.validate(route, calc), [])

    def test_heat_treat_operation(self):
        shaft = PARTS["Вал ступенчатый"]
        for grade, name, fragment, texts in (
            ("45", "Термическая (нормализация)", "Нормализация 845–875 °C на воздухе",
             ["Нагреть поковку до 845–875 °C", "Охладить на воздухе"]),
            ("ШХ15", "Термическая (отжиг)", "Отжиг 780–800 °C с печью",
             ["Нагреть поковку до 780–800 °C", "Охладить поковку с печью"]),
            ("09Г2С", "Термическая (нормализация)", "ГОСТ 8479-70, п. 1.16", ["Охладить на воздухе"]),
        ):
            with self.subTest(grade):
                calc = pipeline.calculate(Part.from_dict({**shaft.to_dict(), "material_grade": grade}))
                route = baseline.build(calc)
                op = next(o for o in route["operations"] if o["code"] == "055_heat_treat")
                self.assertEqual(op["name"], name)
                self.assertIn(fragment, op["rationale"])
                self.assertEqual(validate.validate(route, calc), [])
                got = [t["text"] for t in transitions.baseline_transitions("055_heat_treat", calc)]
                self.assertTrue(set(texts) <= set(got), got)
                self.assertEqual("°C" in " ".join(got), grade != "09Г2С")

    def test_finish_rationale_lowercases_equipment(self):
        self.assertEqual(baseline.lower_first("Кривошипный пресс"), "кривошипный пресс")
        self.assertEqual(baseline.lower_first("КГШП"), "КГШП")
        for name, calc in every_calc():
            for op in baseline.build(calc)["operations"]:
                if op["code"] == "030_finish":
                    tail = op["rationale"].split("оборудование — ", 1)[1]
                    self.assertFalse(tail[0].isupper() and tail[1:2].islower(), name)

    def test_transitions(self):
        for name, calc in every_calc():
            allowed = numbers.collect_allowed(calc.to_dict())
            eq = calc.force.main_equipment
            for row, op in zip(transitions.build(baseline.build(calc), calc, None), baseline.build(calc)["operations"]):
                with self.subTest(name, op=op["code"]):
                    self.assertEqual(row["source"], "baseline")
                    self.assertEqual([t["number"] for t in row["transitions"]],
                                     list(range(1, len(row["transitions"]) + 1)))
                    for t in row["transitions"]:
                        self.assertEqual(numbers.check(t["text"], allowed), [])
                        if eq and op["code"] == "030_finish":
                            self.assertNotIn(eq.name_ru.lower(), t["text"].lower())
                    errors = transitions.sense_errors(row["transitions"], op["code"],
                                                      names.equipment_name(op.get("equipment") or ""),
                                                      facts=transitions.facts_of(calc))
                    self.assertEqual([e for e in errors if "в детали нет такой части" not in e], [])


class Validator(unittest.TestCase):

    def check(self, change, fragment):
        route = copy.deepcopy(baseline.build(GEAR))
        change(route)
        errors = validate.validate(route, GEAR)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_rejects(self):
        def swap_heat_and_finish(r):
            ops = r["operations"]
            i = codes(r).index("010_heat")
            j = codes(r).index("030_finish")
            ops[i], ops[j] = ops[j], ops[i]
            ops[i]["number"], ops[j]["number"] = ops[j]["number"], ops[i]["number"]

        def duplicate(r):
            r["operations"].insert(3, copy.deepcopy(r["operations"][2]))
            for i, op in enumerate(r["operations"]):
                op["number"] = f"{(i + 1) * 5:03d}"

        def shot_blast(r):
            clean_op(r).update(equipment="shot_blast", name="Очистная (дробемётная)")

        cases = [
            (lambda r: r["operations"][2].update(code="099_волшебство"), "нет в справочнике"),
            (lambda r: r["operations"][0].update(equipment="hammer_die"), "недопустимо"),
            (lambda r: r.update(operations=[o for o in r["operations"] if o["code"] != "070_control"]), "070_control"),
            (swap_heat_and_finish, "порядок"),
            (lambda r: r.update(process="free_forging"), "Техпроцесс изменён"),
            (duplicate, "дважды"),
            (lambda r: r["operations"][0].update(name="Отрезная операция"), "не совпадает со справочником"),
            (lambda r: r["operations"][1].update(number="007"), "кратны пяти"),
            (shot_blast, "галтовочном барабане"),
            (lambda r: clean_op(r).update(name="Очистная (дробемётная)"), "называется"),
        ]
        for change, fragment in cases:
            with self.subTest(fragment):
                self.check(change, fragment)

    def test_hammer_needs_preform(self):
        calc = pipeline.calculate(PARTS["Вал ступенчатый"])
        route = baseline.build(calc)
        route["operations"] = [o for o in route["operations"] if o["code"] != "025_preform"]
        for i, op in enumerate(route["operations"]):
            op["number"] = f"{(i + 1) * 5:03d}"
        self.assertTrue(any("025_preform" in e for e in validate.validate(route, calc)))

    def test_extra_fields_dropped(self):
        route = baseline.build(GEAR)
        route["operations"][0]["время_мин"] = 3.5
        route["комментарий"] = "от модели"
        self.assertEqual(validate.validate(route, GEAR), [])
        clean = validate.normalize(route)
        self.assertNotIn("время_мин", clean["operations"][0])
        self.assertNotIn("комментарий", clean)


class SingleRequest(unittest.TestCase):

    def test_parse(self):
        for text in ('{"process": "x", "operations": []}',
                     '```json\n{"process": "x", "operations": []}\n```',
                     'Вот маршрут:\n{"process": "x", "operations": []}\nНадеюсь, помог.'):
            data, err = generate.parse_response(text)
            self.assertIsNone(err)
            self.assertEqual(data["process"], "x")
        data, err = generate.parse_response("извините, я не могу")
        self.assertIsNone(data)
        self.assertTrue(err)

    def test_good_answer(self):
        result = generate.generate_route(GEAR, MockClient([baseline.build(GEAR)]))
        self.assertEqual((result.source, result.attempts, result.errors), ("llm", 1, []))

    def test_retry_after_error(self):
        bad = baseline.build(GEAR)
        bad["operations"][1]["code"] = "010_нагрев"
        client = MockClient([bad, baseline.build(GEAR)])
        steps = []
        result = generate.generate_route(GEAR, client, report=lambda *s: steps.append(s))
        self.assertEqual((result.source, result.attempts, len(result.errors)), ("llm", 2, 1))
        self.assertIn("не прошёл проверку", client.calls[-1][-1]["content"])
        self.assertLess(sum(len(m["content"]) for m in client.calls[1]), sum(len(m["content"]) for m in client.calls[0]))
        self.assertEqual([s[1] for s in steps], [1, 2])

    def test_fallback(self):
        bad = baseline.build(GEAR)
        bad["operations"][0]["code"] = "000_чепуха"
        result = generate.generate_route(GEAR, MockClient([bad, bad, bad]))
        self.assertEqual((result.source, result.attempts), ("baseline", 3))
        self.assertTrue(result.route["operations"])
        self.assertIn("не дала корректный маршрут", result.fallback_reason)
        self.assertIn("Ollama недоступна", generate.generate_route(GEAR, Down()).fallback_reason)
        self.assertEqual(generate.generate_route(GEAR, None).source, "baseline")

    def test_prompt(self):
        for name, calc in every_calc():
            with self.subTest(name):
                self.assertLess(sum(len(m["content"]) for m in prompt.build_route_messages(calc)), 16000)
        self.assertLess(sum(len(m["content"]) for m in prompt.build_route_messages(GEAR, use_examples=False)), 10000)
        block = prompt.render_calc(GEAR)
        for fact in (GEAR.process, "отверстие прошивается", GEAR.part.material_grade):
            self.assertIn(fact, block)
        listing = prompt.render_allowed_operations(GEAR.process)
        self.assertIn("030_finish", listing)
        self.assertNotIn("020_draw", listing)
        examples = prompt.load_examples()
        self.assertGreaterEqual(len(examples), 2)
        self.assertTrue(all(ex["input"] and ex["output"]["operations"] for ex in examples))
        good = prompt.instruction("operation_card").split("Хорошо:", 1)[1].split("Плохо:", 1)[0].lower()
        for word in ("уложить", "удар", "обода"):
            self.assertNotIn(word, good)

    def test_compare(self):
        route = baseline.build(GEAR)
        self.assertEqual(generate.compare(route, route)["match_percent"], 100.0)
        shorter = copy.deepcopy(route)
        shorter["operations"] = [o for o in shorter["operations"] if o["code"] != "015_upset"]
        diff = generate.compare(shorter, route)
        self.assertFalse(diff["identical"])
        self.assertEqual(diff["missing_in_llm"], ["015_upset"])


class Stepwise(unittest.TestCase):

    def test_agreeing_model_gives_baseline_route(self):
        for name, calc in every_calc():
            with self.subTest(name):
                result = stepwise.build(calc, MockClient(rule_answers(calc)))
                self.assertEqual(result.source, "stepwise")
                self.assertEqual(validate.validate(result.route, calc), [])
                self.assertTrue(generate.compare(result.route, baseline.build(calc))["identical"])

    def test_asks_only_applicable(self):
        calc = pipeline.calculate(PARTS["Вал ступенчатый"])
        asked = [d["code"] for d in stepwise.build(calc, MockClient(rule_answers(calc))).decisions]
        self.assertNotIn("065_calibrate", asked)
        self.assertIn("025_preform", asked)

    def test_failure_is_local(self):
        answers = rule_answers(GEAR)
        asked = [s["code"] for s in stepwise.DECISIONS if stepwise._applicable(s["code"], GEAR.process)]
        i = asked.index("065_calibrate")
        answers[i:i + 1] = ["не знаю", "тоже не знаю"]
        result = stepwise.build(GEAR, MockClient(answers))
        by_code = {d["code"]: d for d in result.decisions}
        self.assertEqual(by_code["065_calibrate"]["source"], "baseline")
        self.assertEqual(by_code["015_upset"]["source"], "llm")
        self.assertEqual(validate.validate(result.route, GEAR), [])

    def test_model_can_add_operation(self):
        answers = rule_answers(GEAR)
        asked = [s["code"] for s in stepwise.DECISIONS if stepwise._applicable(s["code"], GEAR.process)]
        answers[asked.index("025_preform")] = {"needed": True, "rationale": "Полотно тонкое, нужен предварительный ручей"}
        result = stepwise.build(GEAR, MockClient(answers))
        self.assertEqual(validate.validate(result.route, GEAR), [])
        self.assertEqual(generate.compare(result.route, baseline.build(GEAR))["extra_in_llm"], ["025_preform"])

    def test_rule_breaking_decision_is_asked_again(self):
        shaft = pipeline.calculate(PARTS["Вал ступенчатый"])
        free = pipeline.calculate(Part.from_dict({**RAW["Фланец"], "annual_program": 50}))
        refuse = {"needed": False, "rationale": "Хватит одного окончательного ручья"}
        no_pierce = {"needed": False, "rationale": "Отверстие сверлится после ковки"}
        for name, calc, code, bad, fixed, source in (
            ("предв. штамповка, исправилась", shaft, "025_preform", refuse, True, "llm"),
            ("предв. штамповка, упорствует", shaft, "025_preform", refuse, False, "baseline"),
            ("прошивка, исправилась", free, "035_pierce", no_pierce, True, "llm"),
        ):
            with self.subTest(name):
                answers = rule_answers(calc)
                asked = [s["code"] for s in stepwise.DECISIONS if stepwise._applicable(s["code"], calc.process)]
                i = asked.index(code)
                answers[i:i + 1] = [bad, answers[i] if fixed else bad]
                result = stepwise.build(calc, MockClient(answers))
                decision = next(d for d in result.decisions if d["code"] == code)
                self.assertEqual((result.source, decision["source"], decision["attempts"]), ("stepwise", source, 2))
                self.assertIn(code, codes(result.route))
                self.assertEqual(validate.validate(result.route, calc), [])

    def test_without_model(self):
        self.assertEqual(stepwise.build(GEAR, None).source, "baseline")
        result = stepwise.build(GEAR, Down())
        self.assertTrue(all(d["source"] == "baseline" for d in result.decisions))
        self.assertEqual(validate.validate(result.route, GEAR), [])

    def test_request_is_small(self):
        client = MockClient(rule_answers(GEAR))
        stepwise.build(GEAR, client)
        full = sum(len(m["content"]) for m in prompt.build_route_messages(GEAR))
        self.assertLess(max(sum(len(m["content"]) for m in call) for call in client.calls), full / 5)
        self.assertLess(len(stepwise.digest(GEAR)), 600)

    def test_decision_parsing(self):
        for value, expected in ((True, True), ("true", True), ("да", True), ("Нужна", True), (1, True),
                                (False, False), ("false", False), ("нет", False), ("не нужна", False), (0, False)):
            errors, clean = stepwise._validate_decision({"needed": value, "rationale": "Поковка круглая в плане"})
            self.assertEqual((errors, clean["needed"]), ([], expected), value)
        errors, clean = stepwise._validate_decision({"needed": True, "rationale": "Поковка круглая в плане",
                                                     "operation": "015_upset", "confidence": 0.9})
        self.assertEqual((errors, clean["needed"]), ([], True))
        errors, clean = stepwise._validate_decision({"answer": {"needed": False, "rationale": "Операция не нужна"}})
        self.assertEqual((errors, clean["needed"]), ([], False))
        self.assertEqual(len(stepwise._validate_decision({"needed": "может быть", "rationale": "ок"})[0]), 2)
        self.assertTrue(stepwise._validate_decision("просто строка")[0])

    def test_raw_responses_kept(self):
        answers = ["не json", "тоже не json"] + [{"needed": False, "rationale": "Операция не требуется"}] * 10
        result = stepwise.build(GEAR, MockClient(answers))
        self.assertEqual(result.decisions[0]["source"], "baseline")
        table = stepwise.decisions_table(result, show_raw=True)
        self.assertIn("Почему модель не приняли", table)
        self.assertIn("не json", table)

    def test_progress(self):
        steps = []
        result = stepwise.build(GEAR, MockClient(rule_answers(GEAR)), report=lambda *s: steps.append(s))
        asked = len(result.decisions)
        self.assertEqual([s[1] for s in steps], list(range(1, asked + 1)))
        self.assertTrue(all(s[2] == asked and s[0].strip() for s in steps))


class Numbers(unittest.TestCase):

    def test_extract(self):
        self.assertEqual(numbers.extract("Нагреть до 1200 °C, выдержать 12,5 мин"), [1200.0, 12.5])
        self.assertEqual(numbers.extract("круг 105 x 176 мм"), [105.0, 176.0])
        self.assertEqual(numbers.extract("без чисел"), [])

    def test_check(self):
        flash = GEAR.blank.mass_flash.value
        for text in (f"Нагреть до {GEAR.heating.temp_start.value:.0f} °C",
                     f"Установить пруток круг {GEAR.blank.diameter.value:g} мм",
                     "Штамповать за 3 удара в 2 приёма",
                     f"Обрезать облой массой {flash:.2f} кг", f"Обрезать облой массой {flash:.1f} кг"):
            self.assertEqual(numbers.check(text, ALLOWED), [], text)
        for text, invented in (("Нагреть до 1350 °C", 1350.0), ("Выдержать 15 мин", 15.0), ("Давление 250 МПа", 250.0)):
            self.assertEqual(numbers.check(text, ALLOWED), [invented])


class Transitions(unittest.TestCase):

    def test_validate(self):
        cases = [
            ("010_heat", [(1, "Загрузить заготовки в печь"), (2, "Нагреть до 1450 °C и выдержать 20 мин")], "1450"),
            ("010_heat", [(1, "Загрузить заготовки в печь"), (5, "Выгрузить заготовку")], "номер должен быть 2"),
            ("030_finish", [(1, "Загрузить заготовки в печь"), (2, "Выгрузить заготовку")], "Код операции изменён"),
        ]
        for operation, items, fragment in cases:
            answer = {"operation": operation, "transitions": [{"number": n, "text": t} for n, t in items]}
            errors = transitions.validate(answer, "010_heat", ALLOWED)
            self.assertTrue(any(fragment in e for e in errors), errors)

    def test_model_answers(self):
        route = baseline.build(GEAR)
        steps = []
        rows = transitions.build(route, GEAR, MockClient(two_transitions(route)), report=lambda *s: steps.append(s))
        self.assertTrue(all(r["source"] == "llm" and len(r["transitions"]) == 2 for r in rows))
        self.assertEqual([s[1] for s in steps], list(range(1, len(rows) + 1)))

    def test_failure_is_local(self):
        route = baseline.build(GEAR)
        answers = two_transitions(route)
        answers[1:2] = ["чепуха", "снова чепуха"]
        rows = transitions.build(route, GEAR, MockClient(answers))
        self.assertEqual([r["source"] for r in rows[:3]], ["llm", "baseline", "llm"])
        self.assertTrue(rows[1]["transitions"])

    def test_bad_answer_falls_back(self):
        answer = json.dumps({"operation": "005_cut", "transitions": [
            {"number": 1, "text": "Резать заготовку на ручном ленточном станке"},
            {"number": 2, "text": "Уложить отрезанные заготовки в окончательный ручей"}]}, ensure_ascii=False)
        op = {"code": "005_cut", "name": "Заготовительная (резка)", "equipment": "saw"}
        result = transitions.ask_operation(op, GEAR, MockClient([answer, answer]), ALLOWED)
        self.assertEqual(result.source, "baseline")
        self.assertTrue(result.transitions)

    def test_capitalized(self):
        answer = json.dumps({"operation": "055_heat_treat", "transitions": [
            {"number": 1, "text": "загрузить поковку в печь"}, {"number": 2, "text": "охладить на воздухе"}]},
            ensure_ascii=False)
        op = {"code": "055_heat_treat", "name": "Термическая", "equipment": "furnace_heat_treat"}
        result = transitions.ask_operation(op, GEAR, MockClient([answer]), ALLOWED)
        self.assertEqual([t["text"] for t in result.transitions], ["Загрузить поковку в печь", "Охладить на воздухе"])

    def test_lever_control_retried(self):
        calc = pipeline.calculate(PARTS["Рычаг"])
        bad, good = (json.dumps({"operation": "070_control", "transitions": [
            {"number": 1, "text": first}, {"number": 2, "text": "Контролировать отсутствие поверхностных дефектов"}]},
            ensure_ascii=False) for first in ("Контролировать диаметр и толщину поковки",
                                              "Контролировать размеры тела рычага и бобышки по чертежу"))
        op = {"code": "070_control", "name": "Контрольная", "equipment": ""}
        result = transitions.ask_operation(op, calc, MockClient([bad, good]), numbers.collect_allowed(calc.to_dict()))
        self.assertEqual((result.source, result.attempts), ("llm", 2))


SHAFT_FACTS = {"blank": (85.0, 389.0), "forging": (67.4, 88.0, 57.4, 426.4, 60, 80, 50), "flash": 1.53,
               "scale": 0.34, "temp_start": 1200.0, "temp_end": 800.0, "upset_ratio": 1.73,
               "part_mass": 11.94, "forging_mass": 15.28, "hold": 85.0, "treat_hold": 176.0,
               "treat_kind": "нормализация", "treat_temp": (845.0, 875.0)}


class SenseChecks(unittest.TestCase):

    REJECTED = [
        ("Резать заготовку на ручном ленточном станке", "005_cut", "Пила дисковая", {}, "станке"),
        ("Уложить отрезанные заготовки в окончательный ручей", "005_cut", "Пила", {}, "ручей"),
        ("Нагреть поковку до 800 °C", "030_finish", HAMMER, {}, "нагре"),
        ("Уложить заготовку в окончательный ручей", "030_finish", "Молот", {}, "установить"),
        ("Пробить перемычку массой 0.06 кг", "045_trim", TRIM, {}, "прошить"),
        ("Обрезать облой массой 0.67 кг", "045_trim", TRIM, {}, "обрубить"),
        ("Разрезать заготовку длиной 155 мм", "005_cut", "Пила дисковая", {}, "отрезать"),
        ("Выдержать заготовку в печи до равнотемпературы", "010_heat", "Печь", {}, "ковочной"),
        ("Прошить отверстие диаметром 41 мм", "030_finish", "КГШП", {}, "отштамповать"),
        ("Поддерживать температуру в пределах 1200 — 800 °C", "010_heat", "Печь", {}, "температурный интервал"),
        ("Отжигать 2 часа", "055_heat_treat", "Печь термическая", {}, "выдержка"),
        ("Охладить в воздухе", "055_heat_treat", "Печь термическая", {}, "на воздухе"),
        ("Перевернуть заготовку, отрезать вторую сторону", "005_cut", "Пила", {}, "перевернуть"),
        ("Оставить в печи на 1 час", "055_heat_treat", "Печь термическая", {}, "выдержать"),
        ("Выдержать поковку в печи 2 часа", "055_heat_treat", "Печь термическая", {}, "минутах"),
        ("Остудить в воздухе", "055_heat_treat", "Печь термическая", {}, "охладить"),
        ("Нагреть заготовку до температуры 800 °C", "055_heat_treat", "Печь термическая", {}, "термообработки"),
        ("Удалить выдру (перемычку)", "005_cut", "Пила дисковая", {}, "выдр"),
        ("Удалить перемычку", "030_finish", PRESS, {}, "перемычк"),
        ("Обрубить облой", "030_finish", PRESS, {}, "обруб"),
        ("Штамповать за три удара", "030_finish", PRESS, {}, "пресс"),
        ("Штамповать за три удара", "030_finish", HAMMER, {}, "ударов"),
        ("Выдержать заготовку при температуре нагрева", "010_heat", "Индукционный нагреватель", {}, "индукционн"),
        ("Установить заготовку", "045_trim", TRIM, {}, "поковка"),
        ("Установить заготовку в галтовочный барабан", "060_clean", "Галтовочный барабан", {}, "поковка"),
        ("Маркировать номер плавки на заготовке", "075_mark", "", {}, "поковка"),
        ("Произвести дробемётную обработку", "060_clean", "Галтовочный барабан", {}, "дробем"),
        ("Удалить облой", "025_preform", HAMMER, {}, "облой только образуется"),
        ("Установить заготовку в протяжной ручей, отрезать облой", "025_preform", HAMMER, {}, "облой только образуется"),
        ("Установить поковку в пресс, отрезать облой", "045_trim", TRIM, {}, "обрубить"),
        ("Снять облой после штамповки", "030_finish", HAMMER, {}, "облой только образуется"),
        ("Штамповать на паровоздушном молоте", "030_finish", HAMMER, {}, "не называют"),
        ("Штамповать окончательно, молот 1000 кг", "030_finish", HAMMER,
         {"equipment_sizes": ((1000.0, "кг"),)}, "характеристики оборудования"),
        ("Штамповать на прессе 16 МН", "030_finish", PRESS, {}, "характеристики оборудования"),
        ("Нагреть поковку до температуры начала ковки", "055_heat_treat", "Печь термическая", {}, "температуру ковки"),
        ("Выдержать при ковочной температуре", "055_heat_treat", "Печь термическая", {}, "температуру ковки"),
        ("Контролировать диаметр и толщину поковки", "070_control", "",
         {"round_part": False, "elements": ("тело рычага", "бобышка")}, "тело рычага, бобышка"),
        ("Контролировать диаметр обода и толщину поковки", "030_finish", "КГШП",
         {"elements": ("диск", "центральное отверстие")}, "обода"),
        ("Контролировать диаметр поковки 85 мм", "070_control", "", {"facts": SHAFT_FACTS}, "размеры заготовки"),
        ("Провести правку поковки длиной 389 мм", "050_straighten", TRIM, {"facts": SHAFT_FACTS}, "размеры заготовки"),
        ("Удалить окалину массой 1.53 кг с поверхности поковки", "060_clean", "Дробемётная камера",
         {"facts": SHAFT_FACTS}, "масса облоя"),
        ("Штамповать окончательно при 800 °C", "030_finish", HAMMER, {"facts": SHAFT_FACTS}, "конец интервала"),
        ("Осадить заготовку до L/D 1.73", "015_upset", PRESS, {"facts": SHAFT_FACTS}, "до осадки"),
        ("Контролировать исходный индекс 16", "070_control", "", {}, "контролируют размеры и массу"),
        ("Контролировать класс точности Т5 и степень сложности С1", "070_control", "", {}, "класс точности"),
        ("Контролировать массу детали", "070_control", "", {}, "детали после штамповки"),
        ("Контролировать массу 11.94 кг", "070_control", "", {"facts": SHAFT_FACTS}, "масса детали"),
        ("Проверить поверхность поковки на шероховатость Ra 3.2", "070_control", "", {}, "шероховатость"),
        ("Контролировать Ra 6.3 поверхности поковки", "070_control", "", {}, "шероховатость"),
        ("Выдержать поковку в печи 85 мин", "055_heat_treat", "Печь термическая", {"facts": SHAFT_FACTS},
         "перед ковкой"),
        ("Выдержать заготовку в печи 176 мин", "010_heat", "Печь", {"facts": SHAFT_FACTS}, "при термообработке"),
        ("Нагреть поковку до 1200 °C", "055_heat_treat", "Печь термическая", {"facts": SHAFT_FACTS}, "вне режима"),
        ("Нагреть поковку до 850 °C", "055_heat_treat", "Печь термическая",
         {"facts": {**SHAFT_FACTS, "treat_temp": None}}, "назначает технолог"),
    ]

    ACCEPTED = [
        ("Контролировать отсутствие облоя и перемычки", "070_control", "", {}),
        ("Обрубить облой массой 1.06 кг", "045_trim", TRIM, {}),
        ("Прошить отверстие, удалить перемычку массой 0.21 кг", "045_trim", TRIM, {}),
        ("Прошить отверстие, удалить выдру", "035_pierce", "Пресс ковочный гидравлический", {}),
        ("Штамповать за несколько ходов пресса", "030_finish", "Кривошипный пресс", {}),
        ("Штамповать окончательно ударами молота", "030_finish", HAMMER, {}),
        ("Нагреть заготовку до 1200 °C", "010_heat", "Индукционный нагреватель", {}),
        ("Выдержать заготовку в печи до ковочной температуры", "010_heat", "Печь нагревательная камерная (газовая)", {}),
        ("Установить заготовку в окончательный ручей", "030_finish", "Кривошипный пресс", {}),
        ("Установить заготовку в предварительный ручей", "025_preform", HAMMER, {}),
        ("Загрузить поковку в печь", "055_heat_treat", "Печь термическая", {}),
        ("Охладить поковку на воздухе", "055_heat_treat", "Печь термическая", {}),
        ("Произвести галтовку, удалить окалину с поверхности поковки", "060_clean", "Галтовочный барабан", {}),
        ("Контролировать диаметр и толщину поковки", "070_control", "", {"round_part": True}),
        ("Контролировать длину тела рычага и диаметр бобышки", "070_control", "",
         {"round_part": False, "elements": ("тело рычага", "бобышка")}),
        ("Контролировать диаметр обода и толщину поковки", "030_finish", "КГШП",
         {"elements": ("обод", "ступица", "центральное отверстие")}),
        ("Контролировать диаметр средней ступени 88 мм и длину поковки 426.4 мм", "070_control", "",
         {"facts": SHAFT_FACTS}),
        ("Установить заготовку ⌀85 мм в печь, нагреть до 1200 °C", "010_heat", "Печь", {"facts": SHAFT_FACTS}),
        ("Штамповать в интервале 1200 — 800 °C", "030_finish", HAMMER, {"facts": SHAFT_FACTS}),
        ("Контролировать массу поковки 15.28 кг", "070_control", "", {"facts": SHAFT_FACTS}),
        ("Выдержать поковку в печи 176 мин", "055_heat_treat", "Печь термическая", {"facts": SHAFT_FACTS}),
        ("Нагреть поковку до 845–875 °C", "055_heat_treat", "Печь термическая", {"facts": SHAFT_FACTS}),
        ("Выдержать заготовку в печи 85 мин", "010_heat", "Печь нагревательная камерная (газовая)",
         {"facts": SHAFT_FACTS}),
        ("Произвести дробемётную обработку, удалить окалину массой 0.34 кг", "060_clean", "Дробемётная камера",
         {"facts": SHAFT_FACTS}),
    ]

    def test_rejected(self):
        for text, code, equipment, kw, fragment in self.REJECTED:
            with self.subTest(text, code=code):
                errors = transitions.sense_errors([{"text": text}], code, equipment, **kw)
                self.assertTrue(any(fragment in e.lower() for e in errors), errors)

    def test_accepted(self):
        for text, code, equipment, kw in self.ACCEPTED:
            with self.subTest(text, code=code):
                self.assertEqual(transitions.sense_errors([{"text": text}], code, equipment, **kw), [])

    def test_trim_needs_web_when_hole(self):
        facts = {**SHAFT_FACTS, "web": 0.21}
        only_flash = [{"text": "Установить поковку на матрицу обрезного штампа"}, {"text": "Обрубить облой массой 0.67 кг"}]
        with_web = only_flash + [{"text": "Прошить отверстие, удалить перемычку массой 0.21 кг"}]
        self.assertTrue(any("перемычк" in e for e in transitions.sense_errors(only_flash, "045_trim", TRIM, facts=facts)))
        self.assertEqual(transitions.sense_errors(with_web, "045_trim", TRIM, facts=facts), [])
        self.assertEqual(transitions.sense_errors(only_flash, "045_trim", TRIM, facts=SHAFT_FACTS), [])

    def test_heat_treat_order_and_kind(self):
        for name, texts, fragment in (
            ("охлаждение до выдержки", ["Охладить поковку на воздухе", "Выдержать поковку в печи 176 мин"], "после нагрева"),
            ("нормализация и отжиг", ["Провести нормализацию поковки", "Провести отжиг поковки"], "одна обработка"),
        ):
            with self.subTest(name):
                errors = transitions.sense_errors([{"text": t} for t in texts], "055_heat_treat", "Печь термическая",
                                                  facts=SHAFT_FACTS)
                self.assertTrue(any(fragment in e for e in errors), errors)

    def test_repeated(self):
        same = [{"text": "Осадить заготовку до размера"}] * 2
        self.assertIn("повторяет", transitions.sense_errors(same, "015_upset", "Кривошипный пресс")[0])


def fake_ollama(test, delay=0.0, status=200, body=b""):
    seen = {"requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen["requests"] += 1
            time.sleep(delay)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(srv.server_close)
    test.addCleanup(srv.shutdown)
    return f"http://127.0.0.1:{srv.server_address[1]}", seen


class FakeResponse:

    def __init__(self, payload):
        self.data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def http_error(code, message):
    return urllib.error.HTTPError("http://127.0.0.1:11434/api/chat", code, "", {},
                                  io.BytesIO(json.dumps({"error": message}).encode("utf-8")))


class Ollama(unittest.TestCase):

    def urlopen(self, handler):
        original = urllib.request.urlopen
        urllib.request.urlopen = handler
        self.addCleanup(setattr, urllib.request, "urlopen", original)

    def tags(self, *installed):
        self.urlopen(lambda request, timeout=None: FakeResponse({"models": [{"name": n} for n in installed]}))

    def chat_error(self, client, make_error) -> str:
        def fail(request, timeout=None):
            raise make_error()
        self.urlopen(fail)
        with self.assertRaises(LLMError) as caught:
            client.chat([{"role": "user", "content": "тест"}])
        return str(caught.exception)

    def test_supports_thinking(self):
        for model, caps, expected in (("думающая:1b", ["completion", "thinking"], True),
                                      ("простая:1b", ["completion"], False),
                                      ("старая:1b", None, None)):
            with self.subTest(model):
                self.urlopen(lambda request, timeout=None, caps=caps: FakeResponse(
                    {"capabilities": caps} if caps is not None else {"license": "x"}))
                self.assertEqual(OllamaClient(model=model).supports_thinking(), expected)
        self.urlopen(lambda request, timeout=None: FakeResponse({"capabilities": []}))
        self.assertTrue(OllamaClient(model="думающая:1b").supports_thinking())

    def test_available(self):
        qwen = OllamaClient(model="qwen2.5:7b-instruct-q4_K_M")
        self.tags("qwen2.5-coder:7b")
        ok, message = qwen.available()
        self.assertFalse(ok)
        self.assertIn("qwen2.5-coder:7b", message)
        self.tags("qwen2.5-coder:7b", "qwen2.5:7b-instruct-q4_K_M")
        self.assertTrue(qwen.available()[0])
        self.tags("qwen2.5:latest")
        self.assertTrue(OllamaClient(model="qwen2.5").available()[0])
        self.tags()
        self.assertIn("ни одной модели", OllamaClient().available()[1])
        self.tags("a:1", "b:2")
        self.assertEqual(OllamaClient().installed(), ["a:1", "b:2"])

    def test_missing_model(self):
        text = self.chat_error(OllamaClient(model="x"), lambda: http_error(404, "model 'x' not found"))
        self.assertIn("not found", text)
        self.assertIn("ollama pull", text)
        self.assertNotIn("ollama serve", text)

    def test_service_down(self):
        text = self.chat_error(OllamaClient(), lambda: urllib.error.URLError("Connection refused"))
        self.assertIn("недоступна", text)
        self.assertIn("ollama serve", text)

    def test_out_of_memory(self):
        def oom():
            return http_error(500, "llama-server reported out-of-memory: cudaMalloc failed: out of memory")
        text = self.chat_error(OllamaClient(), oom)
        self.assertIn("видеопамяти", text)
        self.assertNotIn("ollama pull", text)
        seen = []

        def once(request, timeout=None):
            seen.append(json.loads(request.data.decode("utf-8"))["options"]["num_ctx"])
            if len(seen) == 1:
                raise oom()
            return FakeResponse({"message": {"content": "{}"}})
        self.urlopen(once)
        self.assertEqual(OllamaClient().chat([{"role": "user", "content": "тест"}]), "{}")
        self.assertEqual(seen[1], seen[0] // 2)

    def test_context_size(self):
        client = OllamaClient()
        short = client._context_for([{"content": "тест"}])
        self.assertTrue(2048 <= short < client._context_for([{"content": "т" * 15000}]) <= 8192)

    def test_switching_models_unloads_previous(self):
        calls = []
        self.urlopen(lambda request, timeout=None: calls.append(json.loads(request.data)) or FakeResponse({}))
        server._clients.clear()
        server._last_model = ""
        server.client("первая")
        server.release_previous("первая")
        server.client("вторая")
        self.assertEqual(server.release_previous("вторая"), "первая")
        self.assertEqual(calls, [{"model": "первая", "keep_alive": 0}])
        self.assertEqual(server.release_previous("вторая"), "")

    def test_stoppable_request(self):
        host, _ = fake_ollama(self, body=json.dumps({"message": {"content": '{"needed": true}'}}).encode())
        self.assertEqual(json.loads(OllamaClient(host=host).chat([{"role": "user", "content": "?"}],
                                                                should_stop=lambda: False)), {"needed": True})
        host, _ = fake_ollama(self, status=404, body=json.dumps({"error": "model 'x' not found"}).encode())
        with self.assertRaisesRegex(LLMError, "ollama pull"):
            OllamaClient(host=host, model="x").chat([{"role": "user", "content": "?"}], should_stop=lambda: False)

    def test_answer_length_capped(self):
        llm_client._THINKING["рассуждает:4b"] = True
        self.addCleanup(llm_client._THINKING.pop, "рассуждает:4b", None)
        for model, think, limit in (("обычная:1b", None, 4096), ("обычная:1b", "low", 8192),
                                    ("рассуждает:4b", None, 8192), ("рассуждает:4b", False, 4096)):
            with self.subTest(model=model, think=think):
                payload = OllamaClient(model=model, think=think)._payload([{"role": "user", "content": "?"}], 2048)
                self.assertEqual(payload["options"]["num_predict"], limit)
        for tokens, reason in ((4096, "не остановилась сама"), (2847, "кончилось окно")):
            cut = {"message": {"content": '{"transitions": [{"number": 1, "text": "Уст'}, "done_reason": "length",
                   "eval_count": tokens}
            self.urlopen(lambda request, timeout=None, cut=cut: FakeResponse(cut))
            host, _ = fake_ollama(self, body=json.dumps(cut).encode())
            for name, ask in (("обычный запрос", lambda m: OllamaClient(model="обычная:1b").chat(m)),
                              ("со стопом", lambda m, host=host: OllamaClient(host=host, model="обычная:1b").chat(
                                  m, should_stop=lambda: False))):
                with self.subTest(name, tokens=tokens):
                    with self.assertRaises(LLMError) as caught:
                        ask([{"role": "user", "content": "?"}])
                    self.assertIn(str(tokens), str(caught.exception))
                    self.assertIn(reason, str(caught.exception))

    def test_stop_cuts_request_in_flight(self):
        host, seen = fake_ollama(self, delay=5)
        start = time.monotonic()
        with self.assertRaises(LLMCancelled):
            OllamaClient(host=host).chat([{"role": "user", "content": "?"}],
                                         should_stop=lambda: time.monotonic() - start > 0.3)
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(seen["requests"], 1)


class Stop(unittest.TestCase):

    def test_nothing_asked_after_stop(self):
        inner = MockClient([{"needed": True, "rationale": "x"}])
        with self.assertRaises(LLMCancelled):
            StoppableClient(inner, lambda: True).chat([{"role": "user", "content": "?"}])
        self.assertEqual(inner.calls, [])

    def test_stepwise_keeps_decisions(self):
        model = StopAfter(rule_answers(GEAR), stop_after=2)
        result = stepwise.build(GEAR, StoppableClient(model, lambda: model.stopped))
        sources = [d["source"] for d in result.decisions]
        self.assertEqual(sources[:2], ["llm", "llm"])
        self.assertEqual(set(sources[2:]), {"baseline"})
        self.assertEqual(len(model.mock.calls), 2)
        self.assertIn(STOPPED_TEXT, result.fallback_reason)
        self.assertNotIn("не дала корректный ответ", result.fallback_reason)
        self.assertEqual(validate.validate(result.route, GEAR), [])

    def test_stop_before_any_answer(self):
        result = stepwise.build(GEAR, StoppableClient(MockClient([]), lambda: True))
        self.assertEqual(result.source, "baseline")
        single = generate.generate_route(GEAR, StoppableClient(MockClient([]), lambda: True))
        self.assertEqual(single.source, "baseline")
        self.assertIn("остановлено", single.fallback_reason.lower())

    def test_transitions_keep_finished_operations(self):
        route = baseline.build(GEAR)
        model = StopAfter(two_transitions(route), stop_after=3)
        rows = transitions.build(route, GEAR, StoppableClient(model, lambda: model.stopped))
        sources = [r["source"] for r in rows]
        self.assertEqual(sources[:3], ["llm"] * 3)
        self.assertEqual(set(sources[3:]), {"baseline"})
        self.assertTrue(all(r["transitions"] for r in rows))


class Chat(unittest.TestCase):

    def test_explain_with_calculated_numbers(self):
        text = (f"Исходный индекс {GEAR.forging.initial_index} определён по массе поковки "
                f"{GEAR.forging.mass_actual.value:.2f} кг (ГОСТ 7505-89, табл. 19, п. 2.6).")
        client = MockClient([chat_answer(text, ["ГОСТ 7505-89, п. 2.6"])])
        res = chat.ask("explain", "Почему такой исходный индекс?", client, GEAR)
        self.assertTrue(res.verified)
        self.assertEqual((res.attempts, res.sources), (1, ["ГОСТ 7505-89, п. 2.6"]))
        self.assertIn("ЗНАЧЕНИЯ И ИСТОЧНИКИ", client.calls[0][0]["content"])
        self.assertIn("РАЗМЕРЫ ПОКОВКИ", client.calls[0][0]["content"])

    def test_explain_invented_number(self):
        client = MockClient([chat_answer("Нагрев до 1375 °C."), chat_answer("В расчёте этого нет.")])
        res = chat.ask("explain", "До какой температуры греть?", client, GEAR)
        self.assertEqual((res.attempts, res.verified), (2, True))
        self.assertIn("1375", client.calls[1][-1]["content"])
        res = chat.ask("explain", "Сколько выдержка?", MockClient([chat_answer("Выдержка 47 минут.")] * 2), GEAR)
        self.assertFalse(res.verified)
        self.assertEqual(res.invented, [47.0])
        self.assertTrue(res.answer)

    def test_document_references_are_not_values(self):
        self.assertEqual(chat.invented_numbers("По ГОСТ 7505-89, табл. 19 и п. 2.6, прил. 3", set()), [])
        self.assertEqual(chat.invented_numbers("ГОСТ 3.1118-82, форма 1, графа 8", set()), [])
        self.assertEqual(chat.invented_numbers("уступ 37 мм", set()), [37.0])

    def test_consult_is_unverified(self):
        res = chat.ask("consult", "Какой облойный мостик?",
                       MockClient([chat_answer("Облойный мостик обычно 2–4 мм.")]), None)
        self.assertTrue(res.answer)
        self.assertFalse(res.verified)
        self.assertEqual(res.attempts, 1)

    def test_broken_answers(self):
        res = chat.ask("explain", "?", MockClient(["не json", chat_answer("Масса детали из расчёта.")]), GEAR)
        self.assertEqual(res.attempts, 2)
        res = chat.ask("explain", "?", MockClient([]), GEAR)
        self.assertTrue(res.error)
        self.assertFalse(res.answer)

    def test_history_is_sent(self):
        client = MockClient([chat_answer("Да.")])
        chat.ask("explain", "А осадка?", client, GEAR, baseline.build(GEAR), None,
                 [{"role": "me", "text": "Почему класс Т4?"}, {"role": "ai", "text": "По оборудованию."}])
        self.assertEqual([m["role"] for m in client.calls[0]], ["system", "user", "assistant", "user"])

    def test_edit_remove(self):
        route = baseline.build(GEAR)
        client = MockClient([edit_answer("Убираю осадку.", remove=["015_upset"])])
        res = chat.ask("edit", "Убери осадку", client, GEAR, route, transitions.build(route, GEAR, None))
        new = res.proposal["route"]
        self.assertNotIn("015_upset", codes(new))
        self.assertEqual([o["number"] for o in new["operations"]], [f"{i * 5:03d}" for i in range(1, len(codes(new)) + 1)])
        self.assertEqual([t["code"] for t in res.proposal["transitions"]], codes(new))
        self.assertEqual(res.proposal["changes"], [{"action": "remove", "code": "015_upset", "name": "Осадка", "number": "015"}])
        self.assertEqual(validate.validate(new, GEAR), [])

    def test_edit_add(self):
        client = MockClient([edit_answer("Добавляю правку.", add=[{"code": "050_straighten",
                                                                   "rationale": "Поковка может коробиться"}])])
        res = chat.ask("edit", "Добавь правку", client, GEAR, baseline.build(GEAR), None)
        ops = res.proposal["route"]["operations"]
        self.assertEqual(codes(res.proposal["route"]).index("050_straighten"), codes(res.proposal["route"]).index("045_trim") + 1)
        self.assertEqual(next(o for o in ops if o["code"] == "050_straighten")["name"], "Правка")

    def test_edit_refused(self):
        route = baseline.build(GEAR)
        bad = edit_answer("Убираю обрезку.", remove=["045_trim"])
        client = MockClient([bad, edit_answer("Обрезку облоя убрать нельзя.")])
        res = chat.ask("edit", "Убери обрезку", client, GEAR, route, None)
        self.assertEqual((res.attempts, res.proposal, res.rejected), (2, None, []))
        self.assertIn("045_trim", client.calls[1][-1]["content"])
        res = chat.ask("edit", "Убери обрезку", MockClient([bad, bad]), GEAR, route, None)
        self.assertIsNone(res.proposal)
        self.assertTrue(any("045_trim" in e for e in res.rejected))
        ans = edit_answer("x", remove=["999_magic"], add=[{"code": "050_straighten", "rationale": "при 777 °C"}])
        res = chat.ask("edit", "?", MockClient([ans, ans]), GEAR, route, None)
        self.assertIsNone(res.proposal)
        self.assertIn("999_magic", " ".join(res.rejected))
        self.assertIn("777", " ".join(res.rejected))

    def test_whatif(self):
        client = MockClient([whatif_answer("Меняю программу и обод.", {"annual_program": 2000}, [{"name": "обод", "d": 200}])])
        res = chat.ask("whatif", "А если 2000 шт. и обод 200?", client, GEAR, part_raw=GEAR_RAW, part_parser=Part.from_dict)
        new = res.proposal["part"]
        self.assertEqual(new["annual_program"], 2000)
        self.assertEqual([p["d"] for p in new["primitives"] if p.get("name") == "обод"], [200.0])
        self.assertNotEqual(GEAR_RAW["annual_program"], 2000)
        self.assertEqual([c["label"] for c in res.proposal["changes"]], ["Программа выпуска, шт./год", "обод: диаметр, мм"])

    def test_whatif_rejected(self):
        bad = whatif_answer("x", {"part_type": "shaft"}, [{"name": "колесо", "d": 1}])
        res = chat.ask("whatif", "?", MockClient([bad, bad]), GEAR, part_raw=GEAR_RAW, part_parser=Part.from_dict)
        self.assertIsNone(res.proposal)
        self.assertIn("part_type", " ".join(res.rejected))
        self.assertIn("колесо", " ".join(res.rejected))

        def parser(raw):
            raise ValueError("Неизвестная марка стали: 99Ж")
        ans = whatif_answer("Меняю сталь.", {"material_grade": "99Ж"})
        res = chat.ask("whatif", "А из 99Ж?", MockClient([ans, ans]), GEAR, part_raw=GEAR_RAW, part_parser=parser)
        self.assertTrue(any("99Ж" in r for r in res.rejected))

    def test_compare_variants(self):
        old = pipeline.calculate(Part.from_dict(GEAR_RAW))
        new = pipeline.calculate(Part.from_dict({**GEAR_RAW, "annual_program": 2000}))
        changed = {r["label"] for r in chat.compare_variants(old, new, 9, 10) if r["changed"]}
        self.assertTrue({"Процесс", "Оборудование", "Операций (по правилам)"} <= changed)
        self.assertNotIn("Масса поковки, кг", changed)

    def test_sources(self):
        text = chat.render_sources(GEAR.to_dict())
        self.assertIn("(не ГОСТ)", text)
        self.assertLessEqual(len(text.splitlines()), chat.MAX_SOURCES_LINES)


class Profiles(unittest.TestCase):

    def test_exact_name(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "a.json").write_text(json.dumps({"model": "m:7b", "temperature": 0.05, "num_ctx": 4096,
                                                   "options": {"top_p": 0.8}, "prompt_notes": ["Пиши короче"]},
                                                  ensure_ascii=False), encoding="utf-8")
        (folder / "broken.json").write_text("{не json", encoding="utf-8")
        p = profiles.find("m:7b", [folder])
        self.assertEqual((p.temperature, p.num_ctx), (0.05, 4096))
        self.assertEqual(profiles.find("m:7b-other", [folder]).file, "")
        payload = OllamaClient(model="m:7b", **profiles.client_kwargs(p))._payload(
            [{"role": "system", "content": "Основа"}, {"role": "user", "content": "?"}], 2048)
        self.assertEqual((payload["options"]["temperature"], payload["options"]["top_p"]), (0.05, 0.8))
        self.assertTrue(payload["messages"][0]["content"].endswith("- Пиши короче"))
        self.assertEqual(payload["messages"][1]["content"], "?")

    def test_think_level(self):
        folder = Path(tempfile.mkdtemp())
        for value, sent in (("low", "low"), ("high", "high"), (True, True), (False, False),
                            ("очень", None), (None, None)):
            with self.subTest(value=value):
                (folder / "m.json").write_text(json.dumps({"model": "m:20b", "think": value}), encoding="utf-8")
                payload = OllamaClient(model="m:20b", **profiles.client_kwargs(profiles.find("m:20b", [folder])))._payload(
                    [{"role": "user", "content": "?"}], 2048)
                self.assertEqual(payload.get("think"), sent)

    def test_shipped(self):
        folder = Path(__file__).resolve().parents[2] / "models"
        models = []
        for path in sorted(folder.glob("*.json")):
            models.append(json.loads(path.read_text(encoding="utf-8"))["model"])
            self.assertEqual(profiles.find(models[-1], [folder]).file, str(path))
        self.assertGreaterEqual(len(models), 2)
        self.assertEqual(len(models), len(set(models)))


if __name__ == "__main__":
    unittest.main()
