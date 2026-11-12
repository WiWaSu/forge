from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = Path(tempfile.mkdtemp()) / "settings.json"
HISTORY = Path(tempfile.mkdtemp())
LESSONS_FILE = Path(tempfile.mkdtemp()) / "lessons.json"
EXPORTS = Path(tempfile.mkdtemp())
os.environ.update(FORGE_SETTINGS_FILE=str(SETTINGS_FILE), FORGE_HISTORY_DIR=str(HISTORY),
                  FORGE_LESSONS_FILE=str(LESSONS_FILE), FORGE_EXPORTS_DIR=str(EXPORTS),
                  OLLAMA_HOST="127.0.0.1:9")

from engine import lessons, paths, progress, setup
from engine.api import server
from engine.calc import pipeline, shape
from engine.calc.sources import value_text
from engine.llm import baseline, client as llm_client, profiles, stepwise
from engine.llm.client import DEFAULT_MODEL, OllamaClient
from engine import update
from engine.models import Part
from engine.tests.mock import MockClient

SAMPLES = json.loads((ROOT / "knowledge" / "examples" / "parts.json").read_text(encoding="utf-8"))["parts"]
GEAR = SAMPLES[2]


def post(path, body=None):
    return server.dispatch("POST", path, body or {})


def get(path):
    return server.dispatch("GET", path)


def route(part=None, **kw):
    return post("/api/route", {"part": GEAR if part is None else part, "mode": "baseline", **kw})


def rule_answers(raw) -> list[dict]:
    calc = pipeline.calculate(Part.from_dict(raw))
    answers = []
    for spec in stepwise.DECISIONS:
        if stepwise._applicable(spec["code"], calc.process):
            needed, why = baseline._needed(spec["code"], calc)
            answers.append({"needed": needed, "rationale": why or "по расчёту"})
    return answers


def model_returns(*answers):
    fake = MockClient([a if isinstance(a, str) else json.dumps(a, ensure_ascii=False) for a in answers])
    return fake, mock.patch.object(server, "client", lambda model="": fake)


def env_without(*names):
    drop = {n.upper() for n in names}
    return {k: v for k, v in os.environ.items() if k.upper() not in drop}


def load_app():
    spec = importlib.util.spec_from_file_location("forge_app", ROOT / "app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app


class State(unittest.TestCase):

    def test_state(self):
        status, data = get("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["samples"]), 5)
        self.assertGreaterEqual(len(data["materials"]), 10)
        self.assertEqual({m["code"] for m in data["modes"]}, {"baseline", "single", "stepwise"})
        self.assertTrue(data["gost7505"]["filled"])
        self.assertTrue(data["gost7505"]["note"])
        self.assertIsInstance(data["ollama"]["models"], list)

    def test_every_code_in_routes_has_a_russian_name(self):
        _, state = get("/api/state")
        for raw in SAMPLES:
            _, data = route(raw)
            with self.subTest(raw["name"]):
                self.assertNotEqual(state["processes"][data["calc"]["process"]], data["calc"]["process"])
                for op in data["route"]["operations"]:
                    if op.get("equipment"):
                        self.assertNotEqual(state["equipment"][op["equipment"]], op["equipment"])

    def test_model_catalog(self):
        catalog = get("/api/state")[1]["catalog"]
        self.assertEqual([g["code"] for g in catalog], ["light", "middle", "strong"])
        models = {m["name"]: m for g in catalog for m in g["models"]}
        for name, m in models.items():
            self.assertTrue(server.MODEL_NAME.fullmatch(name), name)
            self.assertGreater(m["size_gb"], 0)
            self.assertTrue(m["about"])
        self.assertTrue(models[DEFAULT_MODEL]["tested"])

    def test_light_endpoints(self):
        self.assertEqual(get("/api/ping"), (200, {"ok": True}))
        status, data = get("/api/ollama")
        self.assertEqual(status, 200)
        self.assertTrue({"ok", "service", "app_installed", "models"} <= set(data["ollama"]))
        status, data = get("/api/чего-нибудь")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    def test_forms(self):
        forms = get("/api/forms")[1]["forms"]
        self.assertEqual(forms["sheet"]["field_width_mm"], 286)
        self.assertEqual(forms["route_card"]["forms"]["1"]["body_rows"], 14)
        self.assertEqual(set(forms["forging_card"]["forms"]), {"1", "1а"})

    def test_ollama_installed_but_not_running(self):
        server._clients.clear()
        calls = []

        def offline(*a, **k):
            calls.append(1)
            raise OSError("нет сервиса")
        with mock.patch.object(setup, "ollama_installed", lambda: True), \
                mock.patch.object(urllib.request, "urlopen", offline):
            ollama = get("/api/state")[1]["ollama"]
        self.assertEqual((ollama["service"], ollama["app_installed"]), (False, True))
        self.assertEqual(len(calls), 1)


class PartValidation(unittest.TestCase):

    def error(self, part):
        status, data = route(part)
        self.assertEqual(status, 400, data)
        return data["error"]

    def test_rejected(self):
        cases = [
            ({}, "наименование"),
            ({**GEAR, "material_grade": "Сталь-которой-нет"}, "отсутствует в справочнике"),
            ({**GEAR, "primitives": []}, "элемент геометрии"),
            ({**GEAR, "part_type": "звездолёт"}, "тип детали"),
            ({**GEAR, "primitives": [{"kind": "cylinder", "d": -10, "l": 50}]}, "отрицательный"),
            ({**GEAR, "primitives": [{"kind": "tube", "d": 100, "d_inner": 120, "l": 50}]}, "внутренний диаметр"),
            ({**GEAR, "annual_program": 0}, "больше нуля"),
            ({**GEAR, "annual_program": 10 ** 12}, "Программа выпуска"),
            ({**GEAR, "primitives": [{"kind": "cylinder", "d": 12121221, "l": 50}]}, "разряд"),
            ({**GEAR, "roughness_ra_um": 2222222222222222}, "Шероховатость"),
            ({**GEAR, "roughness_ra_um": 0.001}, "пределов"),
            ({**SAMPLES[4], "part_type": "shaft"}, "поперёк оси"),
            ({**GEAR, "part_type": "shaft", "primitives": [{"kind": "cylinder", "d": 200, "l": 30}]}, "«шестерня»"),
            ({**GEAR, "part_type": "gear", "primitives": [{"kind": "cylinder", "d": 60, "l": 400}]}, "тип «вал»"),
            ({**GEAR, "part_type": "lever", "primitives": [{"kind": "cylinder", "d": 60, "l": 400}]}, "параллелепипед"),
            ({**SAMPLES[3], "primitives": [{"kind": "cylinder", "d": 220, "l": 50}]}, "нет отверстия"),
        ]
        for part, fragment in cases:
            with self.subTest(fragment):
                self.assertIn(fragment, self.error(part))
        self.assertEqual(post("/api/route", {"part": GEAR, "mode": "магия"})[0], 400)

    def test_accepted(self):
        status, _ = route({**GEAR, "primitives": [{"kind": "cylinder", "d": server.MAX_DIMENSION_MM, "l": 100}]})
        self.assertEqual(status, 200)
        status, data = route({**GEAR, "part_type": "shaft", "primitives": [{"kind": "cylinder", "d": 50, "l": 3000}]})
        self.assertEqual(status, 200)
        self.assertIn("вне области действия", " ".join(data["calc"]["warnings"]))
        for raw in SAMPLES:
            part = Part.from_dict(raw)
            self.assertEqual(shape.problems(part.part_type, part.primitives), [], raw["name"])


class Route(unittest.TestCase):

    def setUp(self):
        progress.clear()

    def test_calc(self):
        status, data = post("/api/calc", {"part": GEAR})
        self.assertEqual(status, 200)
        calc = data["calc"]
        self.assertGreater(calc["blank"]["mass_blank"]["value"], calc["forging"]["mass_actual"]["value"])
        self.assertTrue(calc["non_standard_values"])
        self.assertTrue(calc["material"]["gost"].startswith("ГОСТ"))
        self.assertTrue(calc["stock"]["gost"].startswith("ГОСТ"))

    def test_baseline(self):
        for raw in SAMPLES:
            status, data = route(raw)
            self.assertEqual(status, 200, raw["name"])
            self.assertTrue(data["route"]["operations"])
        _, data = route()
        self.assertEqual((data["source"], data["compare"], data["model"], data["stopped"]), ("baseline", None, "", False))
        self.assertEqual(set(data["timing"]), {"calc", "route", "transitions"})

    def test_model_unavailable(self):
        _, data = post("/api/route", {"part": GEAR, "mode": "single"})
        self.assertTrue(data["route"]["operations"])
        self.assertEqual(data["model"], DEFAULT_MODEL)
        if data["source"] == "baseline":
            self.assertTrue(data["fallback_reason"])
        self.assertEqual(post("/api/route", {"part": GEAR, "mode": "single", "model": "любая:7b"})[1]["model"], "любая:7b")

    def test_job_closed(self):
        route(job="тест-маршрут")
        self.assertTrue(progress.get("тест-маршрут").to_dict()["finished"])

        def broken(part):
            raise RuntimeError("расчёт упал")
        with mock.patch.object(server.pipeline, "calculate", broken):
            self.assertEqual(route(job="job-broken")[0], 500)
        state = post("/api/progress", {"job": "job-broken"})[1]["progress"]
        self.assertTrue(state["finished"])
        self.assertEqual(state["stage"], "ошибка")

    def test_stop_keeps_model_decisions(self):
        answers = rule_answers(GEAR)
        calls = []

        class Model:
            def chat(self, messages):
                calls.append(1)
                if len(calls) == 2:
                    post("/api/cancel", {"job": "тест-стоп"})
                return json.dumps(answers[len(calls) - 1], ensure_ascii=False)
        with mock.patch.object(server, "client", lambda model="": Model()):
            status, data = post("/api/route", {"part": GEAR, "mode": "stepwise", "model": "заглушка", "job": "тест-стоп"})
        self.assertEqual(status, 200)
        self.assertTrue(data["stopped"])
        self.assertEqual(len(calls), 2)
        sources = [d["source"] for d in data["decisions"]]
        self.assertEqual(sources[:2], ["llm", "llm"])
        self.assertEqual(set(sources[2:]), {"baseline"})

    def test_requests_listed_in_progress(self):
        answers = rule_answers(GEAR)
        _, patch = model_returns("не json", *answers)
        with patch:
            self.assertEqual(route(mode="stepwise", model="заглушка:1b", job="job-feed")[0], 200)
        reqs = post("/api/progress", {"job": "job-feed"})[1]["progress"]["requests"]
        self.assertEqual(len(reqs), len(answers) + 1)
        self.assertEqual([(r["stage"], r["attempt"], r["status"]) for r in reqs[:2]],
                         [(reqs[0]["stage"], 1, "брак"), (reqs[0]["stage"], 2, "ок")])
        self.assertTrue(reqs[0]["stage"].startswith("решение"))
        self.assertTrue(all(r["status"] == "ок" and r["sec"] >= 0 for r in reqs[2:]))

    def test_progress(self):
        self.assertFalse(post("/api/progress", {"job": "такого нет"})[1]["progress"]["known"])
        self.assertFalse(post("/api/cancel", {"job": "такого нет"})[1]["cancel"]["known"])
        job = progress.start("тест-шаги")
        job.report("решение: осадка", 2, 6)
        state = progress.get("тест-шаги").to_dict()
        self.assertEqual((state["stage"], state["done"], state["total"], state["finished"]),
                         ("решение: осадка", 2, 6, False))
        job.finish()
        self.assertTrue(progress.get("тест-шаги").to_dict()["finished"])
        for i in range(progress.MAX_JOBS + 5):
            progress.start(f"задание-{i}")
        self.assertLessEqual(progress.count(), progress.MAX_JOBS)


class Sources(unittest.TestCase):

    def test_rows(self):
        order = ["Поковка", "Заготовка", "Нагрев", "Оборудование"]
        for raw in SAMPLES:
            with self.subTest(raw["name"]):
                c = post("/api/calc", {"part": raw})[1]["calc"]
                rows = c["non_standard"]
                listed = {s.split(":")[0] for s in c["non_standard_values"]
                          if not re.match(r"^blank\.material_items\[\d+\]\.mass$", s.split(":")[0])}
                self.assertEqual(listed, {r["path"] for r in rows})
                self.assertEqual([r["group"] for r in rows], sorted((r["group"] for r in rows), key=order.index))
                for r in rows:
                    self.assertIn(r["kind"], ("approx", "expert", "factory", "book", "calc"))
                    self.assertTrue(r["source_text"] or r["books"] or r["kind"] == "calc")
                    text = r["name"] + r["source_text"] + r["value_text"] + r["how"] + " ".join(r["books"])
                    self.assertNotIn("json", text)
                    self.assertIsNone(re.search(r"\d\.\d{3,}", r["value_text"]))
                standards = {n["standard"] for n in c["normative"]}
                self.assertTrue({"ГОСТ 3.1403-85", "ГОСТ 2590-2006", c["material"]["gost"]} <= standards)

    def test_books_and_approx(self):
        books = [b for r in post("/api/calc", {"part": SAMPLES[4]})[1]["calc"]["non_standard"] for b in r["books"]]
        self.assertLess(len(set(books)), len(books))
        rows = [r for r in post("/api/calc", {"part": SAMPLES[0]})[1]["calc"]["non_standard"] if r["group"] == "Оборудование"]
        self.assertEqual((rows[0]["kind"], rows[0]["kind_name"]), ("approx", "сверить"))

    def test_value_text(self):
        for value, unit, text in ((862598.7261, "мм3", "862,6 см³"), (0.0546, "МН", "54,6 кН"),
                                  (18.9179, "МН", "18,92 МН"), (0.8074, "кг", "0,807 кг"), (464.4531, "кг", "464 кг"),
                                  (1200.0, "°C", "1200 °C"), (1521.4, "кг", "1521 кг"), (12500.0, "мм", "12\u00a0500 мм"),
                                  (63.7534, "%", "63,8 %"), (1.5685, "", "1,57")):
            self.assertEqual(value_text(value, unit), text)


class Cards(unittest.TestCase):

    def test_sheets(self):
        _, data = route(transitions=True)
        status, res = post("/api/cards", {"data": data})
        self.assertEqual(status, 200)
        route_sheet, forging_sheet = res["sheets"]
        self.assertEqual((route_sheet["title"], forging_sheet["title"]), ("МК ГОСТ 3.1118", "КТП ГОСТ 3.1403"))
        part = data["calc"]["part"]
        for sheet, code in ((route_sheet, "МК"), (forging_sheet, "КТП")):
            pages = sheet["pages"]
            self.assertGreater(len(pages), 1)
            for number, page in enumerate(pages, 1):
                for row in page:
                    self.assertAlmostEqual(sum(c[0] for c in row["cells"]), 286, places=2)
                self.assertAlmostEqual(sum(r["height_mm"] for r in page), 209.75, places=2)
                texts = [c[1] for r in page for c in r["cells"]]
                self.assertIn(part["designation"], texts)
                self.assertEqual(page[-1]["cells"][0][1], code)
                self.assertEqual(page[5]["cells"][-1][1], str(number))
            self.assertIn(part["name"], [c[1] for r in pages[0] for c in r["cells"]])
            self.assertEqual(pages[0][5]["cells"][-2][1], str(len(pages)))
        text = " ".join(c[1] for p in route_sheet["pages"] for r in p for c in r["cells"])
        for op in data["route"]["operations"]:
            self.assertIn(op["name"], text)

    def test_xlsx(self):
        _, data = route(transitions=True)
        status, res = post("/api/export", {"data": data})
        self.assertEqual(status, 200)
        path = Path(res["path"])
        self.assertEqual((path.parent, path.suffix), (EXPORTS, ".xlsx"))
        with zipfile.ZipFile(path) as z:
            book = z.read("xl/workbook.xml").decode("utf-8")
            first = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
            second = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
        self.assertIn("МК ГОСТ 3.1118", book)
        self.assertIn("КТП ГОСТ 3.1403", book)
        self.assertIn('orientation="landscape"', first)
        for op in data["route"]["operations"]:
            self.assertIn(op["name"], first)
            self.assertIn(op["name"], second)

    def test_export_refused(self):
        status, res = post("/api/export", {"data": post("/api/calc", {"part": GEAR})[1]})
        self.assertEqual(status, 400)
        self.assertIn("маршрут", res["error"])
        self.assertEqual(post("/api/export")[0], 400)
        self.assertTrue(server.EXPORT_NAME.fullmatch("ДП.01.001_карты_2026-10-29_10-00-00.xlsx"))
        for bad in ("../settings.json", "..\\app.py", "a/b.xlsx", "x.txt"):
            self.assertIsNone(server.EXPORT_NAME.fullmatch(bad), bad)


class Update(unittest.TestCase):

    def release(self, tag, asset=True):
        return {"tag_name": tag, "body": "Что нового", "html_url": "https://github.com/WiWaSu/forge/releases/tag/" + tag,
                "assets": [{"name": "FORGE.exe",
                            "browser_download_url": f"https://github.com/WiWaSu/forge/releases/download/{tag}/FORGE.exe"}]
                if asset else []}

    def test_versions(self):
        for text, expected in (("v1.0.1", (1, 0, 1)), ("0.8-beta", (0, 8, 0)), ("1.0", (1, 0, 0)), ("", (0, 0, 0))):
            with self.subTest(text):
                self.assertEqual(update.version_tuple(text), expected)

    def test_check(self):
        for tag, newer in (("v1.0.1", True), ("v1.0", False), ("v0.9", False)):
            with self.subTest(tag):
                info = update.check("1.0", fetch=lambda url, tag=tag: self.release(tag), fresh=True)
                self.assertEqual((info["newer"], info["latest"], info["error"]), (newer, tag[1:], ""))
                self.assertTrue(update.trusted(info["asset_url"]))

        def offline(url):
            raise OSError("нет сети")
        info = update.check("1.0", fetch=offline, fresh=True)
        self.assertFalse(info["newer"])
        self.assertIn("нет сети", info["error"])

        def no_releases(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        info = update.check("1.0", fetch=no_releases, fresh=True)
        self.assertEqual((info["newer"], info["latest"], info["error"]), (False, "", ""))

        def limited(url):
            raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, None)
        info = update.check("1.0", fetch=limited, fresh=True, fallback=lambda: self.release("v1.0.2"))
        self.assertEqual((info["newer"], info["latest"], info["error"]), (True, "1.0.2", ""))
        self.assertTrue(update.trusted(info["asset_url"]))

    def test_trusted(self):
        for url, ok in (("https://github.com/WiWaSu/forge/releases/download/v1.0.1/FORGE.exe", True),
                        ("https://objects.githubusercontent.com/x", True), ("http://github.com/x", False),
                        ("https://evil.example/FORGE.exe", False)):
            with self.subTest(url):
                self.assertEqual(update.trusted(url), ok)

    def test_install_into_exe(self):
        folder = Path(tempfile.mkdtemp())
        exe = folder / "FORGE.exe"
        exe.write_bytes(b"MZ old")
        launched = []
        info = update.check("1.0", fetch=lambda url: self.release("v1.0.1"), fresh=True)
        result = update.install(info, exe=exe, fetch_file=lambda url, target: target.write_bytes(b"MZ new"),
                                launch=launched.append)
        self.assertEqual(result["installing"], "1.0.1")
        self.assertEqual((folder / update.NEW_EXE).read_bytes(), b"MZ new")
        self.assertEqual(launched, [folder / update.SCRIPT])
        self.assertIn(f'move /y "{update.NEW_EXE}" "FORGE.exe"', launched[0].read_text(encoding="utf-8"))
        for bad in (self.release("v1.0", asset=True), self.release("v1.0.1", asset=False)):
            with self.subTest(bad["tag_name"], asset=bool(bad["assets"])):
                with self.assertRaises(ValueError):
                    update.install(update.check("1.0", fetch=lambda url, bad=bad: bad, fresh=True), exe=exe,
                                   fetch_file=lambda url, target: None, launch=launched.append)

    def test_api(self):
        info = {"current": "1.0", "latest": "1.0.1", "newer": True, "notes": "", "page": "", "asset_url": "",
                "can_install": False, "error": ""}
        with mock.patch.object(server.update_mod, "check", lambda fresh=False: info):
            self.assertEqual(get("/api/update"), (200, {"update": info}))
        status, data = post("/api/update/install", {})
        self.assertEqual(status, 400)
        self.assertIn("FORGE.exe", data["error"])


class Settings(unittest.TestCase):

    def test_first_run_then_remembered(self):
        SETTINGS_FILE.unlink(missing_ok=True)
        data = get("/api/settings")[1]
        self.assertTrue(data["first_run"])
        self.assertEqual(data["layouts_ready"], ["A", "B", "C"])
        status, _ = post("/api/settings", {"settings": {
            "layout": "B", "theme": "dark", "model": "qwen2.5:7b-instruct-q4_K_M",
            "use_llm": True, "mode": "single", "transitions": False, "show_requests": False, "лишнее": 1}})
        self.assertEqual(status, 200)
        again = get("/api/settings")[1]
        self.assertFalse(again["first_run"])
        s = again["settings"]
        self.assertEqual((s["layout"], s["theme"], s["model"], s["mode"], s["transitions"], s["show_requests"]),
                         ("B", "dark", "qwen2.5:7b-instruct-q4_K_M", "single", False, False))
        self.assertNotIn("лишнее", s)

    def test_unknown_values(self):
        s = post("/api/settings", {"settings": {"layout": "Z", "theme": "розовая", "mode": "magic", "use_llm": "да",
                                                "show_requests": "нет"}})[1]["settings"]
        self.assertEqual((s["layout"], s["theme"], s["mode"], s["use_llm"], s["show_requests"]),
                         ("A", "system", "stepwise", True, True))
        for accent, kept in (("#3A7BD5", "#3a7bd5"), ("red", ""), ("#12345", ""), (12, "")):
            with self.subTest(accent):
                saved = post("/api/settings", {"settings": {"accent": accent, "check_updates": False}})[1]["settings"]
                self.assertEqual((saved["accent"], saved["check_updates"]), (kept, False))

    def test_broken_file(self):
        SETTINGS_FILE.write_text("{не json", encoding="utf-8")
        self.assertTrue(get("/api/settings")[1]["first_run"])

    def test_think_level_per_model(self):
        s = post("/api/settings", {"settings": {"think": {
            "gpt-oss:20b": "low", "qwen3:8b": False, "плохая": "очень", "": "high"}}})[1]["settings"]
        self.assertEqual(s["think"], {"gpt-oss:20b": "low", "qwen3:8b": False})
        server._clients.clear()
        for supports, model, expected in ((True, "gpt-oss:20b", "low"), (False, "gpt-oss:20b", None),
                                          (True, "qwen3:8b", False), (True, "без-настройки:1b", None)):
            with self.subTest(model=model, supports=supports):
                with mock.patch.object(server.OllamaClient, "supports_thinking", lambda self: supports):
                    self.assertEqual(server.client(model).think, expected)
        post("/api/settings", {"settings": {}})
        server._clients.clear()

    def test_think_kind_fits_model(self):
        for model, think, expected in (("gpt-oss:20b", "high", "high"), ("gpt-oss:20b", False, None),
                                       ("gpt-oss:20b", True, None), ("qwen3:8b", "low", True),
                                       ("qwen3:8b", False, False), ("qwen3:8b", None, None)):
            with self.subTest(model=model, think=think):
                self.assertEqual(profiles.think_for(model, think), expected)


class History(unittest.TestCase):

    def test_lifecycle(self):
        status, data = post("/api/history/save", {"chat": {
            "title": "Новый чат", "part": GEAR, "result": {"calc": {"x": 1}},
            "events": [{"at": "10:00", "kind": "calc", "text": "Расчёт"}]}})
        self.assertEqual(status, 200)
        cid = data["chat"]["id"]
        self.assertTrue(any(c["id"] == cid and c["designation"] == GEAR["designation"]
                            for c in get("/api/history")[1]["chats"]))
        post("/api/history/save", {"chat": {"id": cid, "title": "Шестерня — маршрут", "events": [{"text": "a"}, {"text": "b"}]}})
        chat = post("/api/history/get", {"id": cid})[1]["chat"]
        self.assertEqual((chat["title"], chat["part"]["designation"], len(chat["events"])),
                         ("Шестерня — маршрут", GEAR["designation"], 2))
        post("/api/history/rename", {"id": cid, "title": "Своё имя"})
        self.assertEqual(post("/api/history/get", {"id": cid})[1]["chat"]["title"], "Своё имя")
        self.assertTrue(post("/api/history/delete", {"id": cid})[1]["deleted"])
        self.assertEqual(post("/api/history/get", {"id": cid})[0], 400)

    def test_foreign_paths(self):
        for bad in ("../settings", "..\\x", "abc", "0" * 31 + "/"):
            self.assertEqual(post("/api/history/get", {"id": bad})[0], 400, bad)
            self.assertEqual(post("/api/history/save", {"chat": {"id": bad}})[0], 400, bad)

    def test_list(self):
        (HISTORY / ("f" * 32 + ".json")).write_text("{битый", encoding="utf-8")
        ids = []
        for title in ("первый", "второй"):
            ids.append(post("/api/history/save", {"chat": {"title": title}})[1]["chat"]["id"])
            time.sleep(1.05)
        chats = [c["id"] for c in get("/api/history")[1]["chats"]]
        self.assertNotIn("f" * 32, chats)
        self.assertEqual([c for c in chats if c in ids], ids[::-1])


class Chat(unittest.TestCase):

    def test_explain(self):
        _, patch = model_returns({"answer": "Класс точности Т4 — по оборудованию.", "sources": ["ГОСТ 7505-89, табл. 19"]})
        with patch:
            status, data = post("/api/chat", {"mode": "explain", "question": "Почему Т4?", "part": GEAR,
                                              "model": "чат:7b", "history": [{"role": "me", "text": "привет"}]})
        self.assertEqual(status, 200)
        c = data["chat"]
        self.assertEqual((c["verified"], c["mode_name"], c["model"]), (True, "Объяснить", "чат:7b"))

    def test_whatif(self):
        _, patch = model_returns({"answer": "Пересчитываю на 2000 шт./год.", "changes": {"annual_program": 2000},
                                  "primitives": [], "sources": []})
        with patch:
            status, data = post("/api/chat", {"mode": "whatif", "question": "А если 2000 в год?", "part": GEAR, "model": "чат:7b"})
        self.assertEqual(status, 200)
        prop = data["chat"]["proposal"]
        self.assertEqual((prop["kind"], prop["part"]["annual_program"]), ("whatif", 2000))
        self.assertTrue(prop["result"]["route"]["operations"])
        self.assertTrue(any(r["changed"] and r["label"] == "Процесс" for r in prop["compare"]))

    def test_bad_requests(self):
        for body in ({"mode": "explain", "question": ""}, {"mode": "explain", "question": "?"},
                     {"mode": "magic", "question": "?"}):
            self.assertEqual(post("/api/chat", body)[0], 400, body)


class Lessons(unittest.TestCase):

    def setUp(self):
        LESSONS_FILE.unlink(missing_ok=True)
        self.addCleanup(LESSONS_FILE.unlink, missing_ok=True)

    def add(self, accept=False, **kw):
        body = {"op_code": "045_trim", "op_name": "Обрезная", "part_type": "gear",
                "wrong": "Обрезка поставлена после термообработки.",
                "right": "Обрезку облоя ставят сразу после окончательной штамповки.", "model": "ученик:7b", **kw}
        status, data = post("/api/lessons/add", {"lesson": body})
        self.assertEqual(status, 200)
        if accept:
            post("/api/lessons/update", {"id": data["lesson"]["id"], "changes": {"status": "accepted"}})
        return data["lesson"]

    def test_lifecycle(self):
        x = self.add()
        self.assertEqual((x["status"], x["rule"], x["used"]), ("candidate", x["right"], 0))
        self.assertEqual(get("/api/lessons")[1]["counts"], {"candidate": 1, "accepted": 0, "rejected": 0})
        status, data = post("/api/lessons/update", {"id": x["id"], "changes": {"status": "accepted", "rule": "Обрезка — сразу после штамповки."}})
        self.assertEqual((status, data["lesson"]["status"], data["lesson"]["rule"]), (200, "accepted", "Обрезка — сразу после штамповки."))
        post("/api/lessons/repeat", {"id": x["id"], "model": "ученик:7b"})
        row = next(r for r in get("/api/lessons")[1]["stats"] if r["model"] == "ученик:7b")
        self.assertEqual(row, {"model": "ученик:7b", "marked": 1, "accepted": 1, "repeats_after": 1})
        self.assertTrue(post("/api/lessons/delete", {"id": x["id"]})[1]["deleted"])
        self.assertEqual(get("/api/lessons")[1]["lessons"], [])

    def test_bad_input(self):
        status, data = post("/api/lessons/add", {"lesson": {"right": "да"}})
        self.assertEqual(status, 400)
        self.assertIn("как правильно", data["error"])
        self.assertEqual(post("/api/lessons/add", {"lesson": {"right": "длинный текст урока", "op_code": "../x"}})[0], 400)
        self.assertEqual(post("/api/lessons/update", {"id": "0" * 32, "changes": {}})[0], 400)
        self.assertEqual(post("/api/lessons/update", {"id": self.add()["id"], "changes": {"status": "забыть"}})[0], 400)

    def test_only_matching_lessons_are_sent(self):
        trim = self.add(accept=True)
        shaft = self.add(accept=True, part_type="shaft", right="Для валов — свой урок про протяжку.", op_code="020_draw")
        general = self.add(accept=True, op_code="", part_type="", right="Обоснование — одна фраза, без ссылок на пункты.")
        self.add(right="Этот урок ещё на проверке и уйти не должен.")
        fake = MockClient(["{}"] * 3)
        teacher = lessons.LessonsClient(fake, "gear")

        def ask(user):
            teacher.chat([{"role": "system", "content": "СИСТЕМА"}, {"role": "user", "content": user}])
            return fake.calls[-1][0]["content"]
        sent = ask("ОПЕРАЦИЯ, которую нужно раскрыть:\n  Код: 045_trim\n")
        self.assertIn("УРОКИ ИЗ ЖУРНАЛА ОШИБОК", sent)
        self.assertIn(trim["right"][:20], sent)
        self.assertIn("одна фраза", sent)
        self.assertNotIn("протяжку", sent)
        self.assertNotIn("на проверке", sent)
        self.assertNotIn(trim["right"][:20], ask("ОПЕРАЦИЯ, которую нужно раскрыть:\n  Код: 070_control\n"))
        pierce = next(d for d in stepwise.DECISIONS if d["code"] == "035_pierce")["question"]
        self.assertNotIn(trim["right"][:20], ask(f"ДЕТАЛЬ\n\nВОПРОС: {pierce}\n"))
        self.assertEqual({x["id"] for x in teacher.summary()}, {trim["id"], general["id"]})
        teacher.finish()
        used = {x["id"]: x["used"] for x in get("/api/lessons")[1]["lessons"]}
        self.assertEqual((used[trim["id"]], used[general["id"]], used[shaft["id"]]), (1, 1, 0))

    def test_route_and_chat_carry_lessons(self):
        x = self.add(accept=True, op_code="", part_type=GEAR["part_type"], right="Урок для всего маршрута этой детали.")
        fake, patch = model_returns(*rule_answers(GEAR))
        with patch:
            status, data = post("/api/route", {"part": GEAR, "mode": "stepwise", "model": "ученик:7b"})
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in data["lessons"]], [x["id"]])
        self.assertTrue(all("Урок для всего маршрута" in c[0]["content"] for c in fake.calls))
        self.assertEqual(route()[1]["lessons"], [])
        fake, patch = model_returns({"answer": "Класс точности Т4.", "sources": []})
        with patch:
            data = post("/api/chat", {"mode": "explain", "question": "Почему Т4?", "part": GEAR, "model": "ученик:7b"})[1]
        self.assertEqual(data["chat"]["lessons"][0]["id"], x["id"])
        self.assertIn("Урок для всего маршрута", fake.calls[0][0]["content"])

    def test_starter_lessons(self):
        self.assertEqual(lessons.add_starter(), 4)
        data = get("/api/lessons")[1]
        self.assertEqual((data["counts"]["accepted"], data["stats"]), (4, []))
        self.assertEqual({x["op_code"] for x in data["lessons"]}, {"025_preform", "055_heat_treat", "070_control", ""})
        self.assertEqual(lessons.add_starter(), 0)
        post("/api/lessons/delete", {"id": data["lessons"][0]["id"]})
        self.assertEqual(lessons.add_starter(), 0)
        self.assertTrue(any(x["op_code"] == "070_control" for x in lessons.accepted_for("lever")))
        self.assertFalse(any(x["op_code"] == "070_control" for x in lessons.accepted_for("gear")))


class OllamaSetup(unittest.TestCase):

    def test_status_and_unknown_step(self):
        self.assertFalse(get("/api/setup")[1]["setup"]["running"])
        self.assertTrue(post("/api/setup", {"step": "переустановить-винду"})[1]["setup"]["error"])
        for model in ("--help", "qwen2.5:7b; del x"):
            self.assertEqual(post("/api/setup", {"step": "model", "model": model})[0], 400, model)

    def test_commands(self):
        command, target = setup._command("ollama", "неважно")
        self.assertEqual((command[0], target), ("winget", "Ollama.Ollama"))
        command, target = setup._command("model", "qwen2.5:7b")
        self.assertEqual(Path(command[0]).stem.lower(), "ollama")
        self.assertEqual((command[1:], target), (["pull", "qwen2.5:7b"], "qwen2.5:7b"))
        flags = setup._no_window()
        self.assertTrue(flags == {} or flags["creationflags"] & setup.CREATE_NEW_PROCESS_GROUP)

    def test_start_runs_app_with_its_models_folder(self):
        folder = Path(tempfile.mkdtemp())
        exe = folder / "ollama.exe"
        exe.write_text("")
        with mock.patch.object(setup, "ollama_path", lambda: str(exe)):
            self.assertEqual(setup._command("start", "")[0], [str(exe), "serve"])
            (folder / "ollama app.exe").write_text("")
            self.assertEqual(setup._command("start", "")[0], [str(folder / "ollama app.exe")])

    def test_missing_tool(self):
        with mock.patch.object(shutil, "which", lambda name: None), mock.patch.object(setup, "ollama_path", lambda: None):
            ollama = setup.start("ollama", "любая")
            model = setup.start("model", "любая")
        self.assertIn("winget", ollama["error"])
        self.assertIn("ollama", model["error"].lower())

    def test_ollama_found_without_path(self):
        for env_key, sub in (("LOCALAPPDATA", ("Programs", "Ollama")), ("ProgramFiles", ("Ollama",))):
            with self.subTest(env_key):
                tmp = tempfile.mkdtemp()
                exe = Path(tmp, *sub, "ollama.exe")
                exe.parent.mkdir(parents=True)
                exe.write_text("")
                env = env_without("LOCALAPPDATA", "ProgramW6432", "ProgramFiles")
                with mock.patch.object(shutil, "which", lambda name: None), \
                        mock.patch.dict(os.environ, {**env, env_key: tmp}, clear=True):
                    self.assertEqual(setup.ollama_path(), str(exe))

    def test_default_host(self):
        for value, expected in ((None, "http://127.0.0.1:11434"), ("0.0.0.0", "http://127.0.0.1:11434"),
                                ("127.0.0.1:9000", "http://127.0.0.1:9000")):
            env = env_without("OLLAMA_HOST")
            if value:
                env["OLLAMA_HOST"] = value
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(llm_client._default_host(), expected)

    def test_model_details(self):
        payload = json.dumps({"models": [
            {"name": "b:7b", "size": 4_700_000_000, "details": {"parameter_size": "7.6B"}},
            {"name": "a:1b", "size": 1_200_000_000, "details": {"parameter_size": "1.5B"}}]}).encode()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = payload
        with mock.patch.object(urllib.request, "urlopen", return_value=response):
            models = OllamaClient().installed_details()
        self.assertEqual([m["name"] for m in models], ["a:1b", "b:7b"])
        self.assertEqual((models[1]["size_gb"], models[1]["parameters"]), (4.7, "7.6B"))


class App(unittest.TestCase):

    def setUp(self):
        self.app = load_app()

    def test_data_root(self):
        self.assertEqual(paths.data_root(), ROOT)
        self.assertFalse(paths.frozen())
        with mock.patch.object(sys, "_MEIPASS", "/tmp/forge-bundle", create=True):
            self.assertEqual(paths.html(), Path("/tmp/forge-bundle/html"))

    def test_browser_paths_follow_environment(self):
        env = env_without("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
        env.update({"ProgramFiles": r"D:\Apps", "ProgramFiles(x86)": r"D:\Apps86", "LOCALAPPDATA": r"E:\Users\Иван\AppData\Local"})
        with mock.patch.dict(os.environ, env, clear=True):
            found = self.app.browser_candidates()
        self.assertFalse(any(p.startswith("C:") for p in found))
        self.assertTrue(any("Иван" in p and "chrome.exe" in p for p in found))

    def test_window_profile_per_launch(self):
        self.assertIn(str(os.getpid()), str(self.app.window_profile()))

    def test_waits_for_page_after_browser_handoff(self):
        notes = []
        self.app.log = notes.append
        self.app.SILENCE_SECONDS = 1.5
        server._touch()
        start = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.wait_for_window(subprocess.Popen([sys.executable, "-c", "pass"]))
        self.assertGreaterEqual(time.monotonic() - start, 1.4)
        self.assertTrue(any("слежу за страницей" in n for n in notes))

    def test_stops_when_window_closes(self):
        self.app.log = lambda text: None
        self.app.HANDOFF_SECONDS = 0.2
        self.app.SILENCE_SECONDS = 30
        start = time.monotonic()
        self.app.wait_for_window(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"]))
        self.assertLess(time.monotonic() - start, 5)

    def test_console_without_stdout(self):
        with mock.patch.object(sys, "stdout", None):
            self.app.safe_console()


class Http(unittest.TestCase):

    def setUp(self):
        quiet = mock.patch.object(server.Handler, "log_message", lambda *a: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def fetch(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as r:
            return r.status, r.read().decode("utf-8")

    def test_page_and_api(self):
        status, html = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn("<title>FORGE", html)
        self.assertEqual(re.findall(r"<script[^>]*>", html), ['<script src="app.js">'])
        for name in ("/", "/app.js", "/app.css"):
            body = self.fetch(name)[1]
            for external in ("https://", "//cdn", "fonts.googleapis"):
                self.assertNotIn(external, body, name)
        self.assertIn("samples", json.loads(self.fetch("/api/state")[1]))

    def test_path_traversal(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/../engine/models.py")
        caught.exception.close()
        self.assertEqual(caught.exception.code, 404)

    def test_foreign_requests(self):
        refused = server.request_refused
        self.assertEqual(refused("POST", "127.0.0.1:8765", "", "application/json", 8765), "")
        self.assertEqual(refused("POST", "localhost:8765", "http://localhost:8765", "application/json; charset=utf-8", 8765), "")
        self.assertEqual(refused("GET", "127.0.0.1:8765", "", "", 8765), "")
        self.assertTrue(refused("POST", "127.0.0.1:8765", "https://evil.example", "application/json", 8765))
        self.assertTrue(refused("POST", "evil.example:8765", "", "application/json", 8765))
        self.assertTrue(refused("POST", "127.0.0.1:8765", "", "text/plain", 8765))


if __name__ == "__main__":
    unittest.main()
