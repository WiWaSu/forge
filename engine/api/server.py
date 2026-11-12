from __future__ import annotations

import dataclasses
import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .. import __version__
from ..calc import pipeline, shape
from ..data import loader
from ..llm import baseline, generate, profiles, stepwise, transitions as transitions_mod
from ..llm.client import DEFAULT_MODEL, OllamaClient, StoppableClient, TrackedClient
from ..models import Part, Primitive
from .. import names
from .. import paths
from .. import progress as progress_mod
from .. import setup as setup_mod
from .. import workspace
from .. import lessons
from .. import update as update_mod
from ..export import cards as cards_mod, xlsx as xlsx_mod

HTML_DIR = paths.html()
SAMPLES = paths.knowledge() / "examples" / "parts.json"

MODES = ["baseline", "single", "stepwise"]

_client_lock = threading.Lock()
_clients: dict[str, OllamaClient] = {}
_last_model = ""


def client(model: str = DEFAULT_MODEL) -> OllamaClient:
    with _client_lock:
        if model not in _clients:
            _clients[model] = OllamaClient(model=model,
                                           **profiles.client_kwargs(profiles.find(model)))
        c = _clients[model]
    chosen = workspace.load_settings()["settings"]["think"]
    c.think = profiles.think_for(model, chosen[model] if model in chosen else profiles.find(model).think)
    if c.think is not None and c.supports_thinking() is False:
        c.think = None
    return c


def release_previous(model: str) -> str:
    global _last_model
    with _client_lock:
        previous, _last_model = _last_model, model
        stale = _clients.get(previous) if previous and previous != model else None
    return previous if stale is not None and stale.unload() else ""


class BadRequest(ValueError):
    pass


PRIMITIVE_FIELDS = {"kind", "d", "d_inner", "l", "a", "b", "c", "subtract", "along_axis", "name"}
PART_TYPES = {"shaft", "gear", "flange", "ring", "lever", "cross", "combined"}

MAX_DIMENSION_MM = 20_000.0
MIN_ROUGHNESS_UM = 0.025
MAX_ROUGHNESS_UM = 200.0
MAX_PROGRAM = 20_000_000


def parse_part(raw: Any) -> Part:
    if not isinstance(raw, dict):
        raise BadRequest("Ожидался объект с описанием детали")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise BadRequest("Не заполнено наименование детали")

    part_type = raw.get("part_type")
    if part_type not in PART_TYPES:
        raise BadRequest(f"Неизвестный тип детали: {part_type}")

    grade = str(raw.get("material_grade") or "").strip()
    try:
        loader.material(grade)
    except loader.ReferenceError as exc:
        raise BadRequest(str(exc)) from exc

    primitives_raw = raw.get("primitives") or []
    if not isinstance(primitives_raw, list) or not primitives_raw:
        raise BadRequest("Нужен хотя бы один элемент геометрии")

    primitives: list[Primitive] = []
    for i, item in enumerate(primitives_raw, 1):
        if not isinstance(item, dict):
            raise BadRequest(f"Элемент {i}: ожидался объект")
        unknown = set(item) - PRIMITIVE_FIELDS
        if unknown:
            raise BadRequest(f"Элемент {i}: неизвестные поля {', '.join(sorted(unknown))}")
        kind = item.get("kind")
        if kind not in ("cylinder", "tube", "box"):
            raise BadRequest(f"Элемент {i}: неизвестный тип {kind}")
        data = {k: item.get(k) for k in PRIMITIVE_FIELDS if k in item}
        for key in ("d", "d_inner", "l", "a", "b", "c"):
            if key in data:
                try:
                    data[key] = float(data[key] or 0)
                except (TypeError, ValueError):
                    raise BadRequest(f"Элемент {i}: размер '{key}' не число")
                if data[key] < 0:
                    raise BadRequest(f"Элемент {i}: размер '{key}' отрицательный")
                if data[key] > MAX_DIMENSION_MM:
                    raise BadRequest(
                        f"Элемент {i}: размер '{key}' равен {data[key]:.0f} мм — "
                        f"это {data[key] / 1000:.0f} м. Больше {MAX_DIMENSION_MM / 1000:.0f} м "
                        f"поковок не делают ни на одном прессе; проверь, не лишний ли разряд"
                    )
        primitive = Primitive(**data)
        if kind == "cylinder" and not (primitive.d and primitive.l):
            raise BadRequest(f"Элемент {i} (цилиндр): нужны диаметр и длина")
        if kind == "tube" and not (primitive.d and primitive.l):
            raise BadRequest(f"Элемент {i} (труба): нужны наружный диаметр и длина")
        if kind == "tube" and primitive.d_inner >= primitive.d:
            raise BadRequest(f"Элемент {i} (труба): внутренний диаметр не меньше наружного")
        if kind == "box" and not (primitive.a and primitive.b and primitive.c):
            raise BadRequest(f"Элемент {i} (параллелепипед): нужны все три размера")
        primitives.append(primitive)

    mismatch = shape.problems(part_type, primitives)
    if mismatch:
        kind = shape.TYPE_NAMES[part_type]
        raise BadRequest(
            f"Тип детали «{kind}» не совпадает с геометрией: " + "; ".join(mismatch)
            + ". Исправьте тип или элементы геометрии — иначе расчёт пойдёт "
            "по технологии другой детали"
        )

    program_raw = raw.get("annual_program")
    if program_raw in (None, ""):
        program_raw = 1000
    try:
        program = int(program_raw)
    except (TypeError, ValueError):
        raise BadRequest("Программа выпуска должна быть целым числом")
    if program <= 0:
        raise BadRequest("Программа выпуска должна быть больше нуля")
    if program > MAX_PROGRAM:
        raise BadRequest(
            f"Программа выпуска {program} шт./год слишком велика — "
            f"проверь число, допустимо до {MAX_PROGRAM} шт./год"
        )

    roughness_raw = raw.get("roughness_ra_um")
    if roughness_raw in (None, ""):
        roughness_raw = 6.3
    try:
        roughness = float(roughness_raw)
    except (TypeError, ValueError):
        raise BadRequest("Шероховатость должна быть числом")
    if roughness <= 0:
        raise BadRequest("Шероховатость должна быть больше нуля")
    if not (MIN_ROUGHNESS_UM <= roughness <= MAX_ROUGHNESS_UM):
        raise BadRequest(
            f"Шероховатость Ra = {roughness} мкм вне разумных пределов "
            f"({MIN_ROUGHNESS_UM}…{MAX_ROUGHNESS_UM} мкм). Для поковок под механическую "
            f"обработку это обычно 3,2…12,5 мкм"
        )

    return Part(
        name=name,
        part_type=part_type,
        material_grade=grade,
        primitives=primitives,
        designation=str(raw.get("designation") or "").strip(),
        annual_program=program,
        envelope_kind="box" if raw.get("envelope_kind") == "box" else "cylinder",
        roughness_ra_um=roughness,
        machined_all_over=bool(raw.get("machined_all_over", True)),
        heating="induction" if raw.get("heating") == "induction" else "flame",
        notes=str(raw.get("notes") or ""),
    )


def ollama_state() -> dict[str, Any]:
    c = client()
    s = c.status()
    s["models"] = [{**m, "tested": bool(profiles.find(m["name"]).file),
                    "thinking": OllamaClient(model=m["name"]).supports_thinking(),
                    "think_profile": profiles.find(m["name"]).think} for m in s["models"]]
    return {
        "ok": s["ok"],
        "message": s["message"],
        "model": c.model,
        "service": s["running"],
        "app_installed": s["running"] or setup_mod.ollama_installed(),
        "installed": [m["name"] for m in s["models"]],
        "models": s["models"],
        "site": setup_mod.OLLAMA_SITE,
    }


def state() -> dict[str, Any]:
    tables = loader.gost7505()["_meta"]
    return {
        "version": __version__,
        "ollama": ollama_state(),
        "gost7505": {
            "filled": bool(tables.get("filled")),
            "verified": bool(tables.get("verified")),
            "note": "Таблицы восстановлены по двум перепечаткам стандарта и сверены между "
                    "собой; сверка по оригиналу не выполнена"
            if not tables.get("verified") else "Сверено по оригиналу",
        },
        "materials": [
            {"grade": m["grade"], "gost": m["gost"], "note": m.get("note", "")}
            for m in loader.load("materials.json")["materials"]
        ],
        "part_types": [
            {"code": t["code"], "description": t["name"]}
            for t in loader.kr_part_types()
        ],
        "processes": dict(names.PROCESS_NAMES),
        "equipment": {
            e["type"]: e["name_ru"]
            for e in loader.load("equipment.json")["equipment"]
            if e.get("type") and e.get("name_ru")
        },
        "samples": json.loads(SAMPLES.read_text(encoding="utf-8"))["parts"],
        "catalog": [
            {**g, "models": [{**m, "tested": bool(profiles.find(m["name"]).file)} for m in g["models"]]}
            for g in loader.load("llm_catalog.json")["groups"]
        ],
        "modes": [
            {"code": "baseline", "name": "Правила", "hint": "Без нейросети, детерминированный маршрут"},
            {"code": "single", "name": "Один запрос", "hint": "Модель строит весь маршрут целиком"},
            {"code": "stepwise", "name": "По частям", "hint": "Отдельный запрос на каждое спорное решение"},
        ],
    }


def calc_only(payload: dict[str, Any]) -> dict[str, Any]:
    part = parse_part(payload.get("part"))
    return {"calc": pipeline.calculate(part).to_dict()}


def route(payload: dict[str, Any]) -> dict[str, Any]:
    part = parse_part(payload.get("part"))
    mode = payload.get("mode") or "baseline"
    if mode not in MODES:
        raise BadRequest(f"Неизвестный режим '{mode}'. Допустимы: {', '.join(MODES)}")

    model = str(payload.get("model") or "").strip() or DEFAULT_MODEL
    llm = client(model)
    if mode != "baseline":
        release_previous(model)

    job_id = str(payload.get("job") or "").strip()
    job = progress_mod.start(job_id) if job_id else None
    report = job.report if job else None
    asker = TrackedClient(StoppableClient(llm, lambda: job.cancelled), job) if job else llm
    taught = lessons.LessonsClient(asker, part.part_type) if mode != "baseline" else None
    if taught is not None:
        asker = taught

    started = time.monotonic()
    done = stopped = False
    try:
        calc = pipeline.calculate(part)
        after_calc = time.monotonic()

        if mode == "baseline":
            result = generate.RouteResult(route=baseline.build(calc), source="baseline")
        elif mode == "single":
            result = generate.generate_route(calc, asker, report=report)
        else:
            result = stepwise.build(calc, asker, report=report)
        after_route = time.monotonic()

        rows = []
        if payload.get("transitions"):
            rows = transitions_mod.build(
                result.route, calc, asker if mode != "baseline" else None, report=report
            )

        reference = result.route if mode == "baseline" else baseline.build(calc)
        finished = time.monotonic()
        stopped = bool(job and job.cancelled)
        taught_list = taught.summary() if taught is not None else []
        done = True
    finally:
        if taught is not None:
            taught.finish()
        if job:
            job.report(("остановлено" if stopped else "готово") if done else "ошибка", 0, 0)
            job.finish()

    return {
        "stopped": stopped,
        "calc": calc.to_dict(),
        "mode": mode,
        "model": model if mode != "baseline" else "",
        "route": result.route,
        "source": result.source,
        "attempts": result.attempts,
        "decisions": result.decisions,
        "errors": result.errors,
        "fallback_reason": result.fallback_reason,
        "compare": generate.compare(result.route, reference)
        if result.source in ("llm", "stepwise") else None,
        "transitions": rows,
        "lessons": taught_list,
        "elapsed_sec": round(finished - started, 1),
        "timing": {
            "calc": round(after_calc - started, 1),
            "route": round(after_route - after_calc, 1),
            "transitions": round(finished - after_route, 1),
        },
    }


def settings_get(payload: dict[str, Any]) -> dict[str, Any]:
    return workspace.load_settings()


def settings_save(payload: dict[str, Any]) -> dict[str, Any]:
    return workspace.save_settings(payload.get("settings") or {})


def history_list(payload: dict[str, Any]) -> dict[str, Any]:
    return {"chats": workspace.list_chats()}


def history_get(payload: dict[str, Any]) -> dict[str, Any]:
    return {"chat": workspace.get_chat(str(payload.get("id") or ""))}


def history_save(payload: dict[str, Any]) -> dict[str, Any]:
    return {"chat": workspace.save_chat(payload.get("chat") or {})}


def history_rename(payload: dict[str, Any]) -> dict[str, Any]:
    return {"chat": workspace.rename_chat(str(payload.get("id") or ""),
                                          str(payload.get("title") or ""))}


def history_delete(payload: dict[str, Any]) -> dict[str, Any]:
    return {"deleted": workspace.delete_chat(str(payload.get("id") or ""))}


def chat(payload: dict[str, Any]) -> dict[str, Any]:
    from ..llm import chat as chat_mod
    mode = str(payload.get("mode") or "explain")
    if mode not in chat_mod.MODES:
        raise BadRequest(f"Неизвестный режим чата '{mode}'")
    question = str(payload.get("question") or "").strip()
    if not question:
        raise BadRequest("Пустой вопрос")

    raw_part = payload.get("part")
    calc = pipeline.calculate(parse_part(raw_part)) if raw_part else None
    if mode in ("explain", "edit", "whatif") and calc is None:
        raise BadRequest("Сначала посчитайте деталь")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if mode == "edit" and not (result.get("route") or {}).get("operations"):
        raise BadRequest("Править нечего: сначала постройте маршрут")
    history = [h for h in (payload.get("history") or []) if isinstance(h, dict)]

    model = str(payload.get("model") or "").strip() or DEFAULT_MODEL
    llm = client(model)
    release_previous(model)
    job_id = str(payload.get("job") or "").strip()
    job = progress_mod.start(job_id) if job_id else None
    asker = TrackedClient(StoppableClient(llm, lambda: job.cancelled), job) if job else llm
    part_type = str(raw_part.get("part_type") or "") if isinstance(raw_part, dict) else ""
    taught = lessons.LessonsClient(asker, part_type)
    asker = taught
    equipment_names = {e["type"]: e["name_ru"] for e in loader.load("equipment.json")["equipment"]
                       if e.get("type") and e.get("name_ru")}
    try:
        answer = chat_mod.ask(mode, question, asker, calc, result.get("route"),
                              result.get("transitions"), history, equipment_names,
                              part_raw=raw_part if isinstance(raw_part, dict) else None,
                              part_parser=parse_part)
    finally:
        taught.finish()
        if job:
            job.finish()
    if mode == "whatif" and answer.proposal and calc is not None:
        variant = route({"part": answer.proposal["part"], "mode": "baseline"})
        calc_new = pipeline.calculate(parse_part(answer.proposal["part"]))
        ops_old = len(baseline.build(calc)["operations"])
        answer.proposal["compare"] = chat_mod.compare_variants(
            calc, calc_new, ops_old, len(variant["route"]["operations"]))
        answer.proposal["result"] = variant
    return {"chat": {**answer.to_dict(), "model": model, "lessons": taught.summary()}}


def lessons_list(payload: dict[str, Any]) -> dict[str, Any]:
    return lessons.listing()


def lessons_add(payload: dict[str, Any]) -> dict[str, Any]:
    return {"lesson": lessons.add(payload.get("lesson") or {})}


def lessons_update(payload: dict[str, Any]) -> dict[str, Any]:
    return {"lesson": lessons.update(str(payload.get("id") or ""), payload.get("changes") or {})}


def lessons_delete(payload: dict[str, Any]) -> dict[str, Any]:
    return {"deleted": lessons.delete(str(payload.get("id") or ""))}


def lessons_repeat(payload: dict[str, Any]) -> dict[str, Any]:
    return {"lesson": lessons.repeat(str(payload.get("id") or ""), str(payload.get("model") or ""))}


def cancel(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("job") or "").strip()
    known = progress_mod.cancel(job_id) if job_id else False
    return {"cancel": {"job": job_id, "known": known}}


def progress(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("job") or "").strip()
    job = progress_mod.get(job_id) if job_id else None
    if job is None:
        return {"progress": {"job": job_id, "known": False}}
    return {"progress": {**job.to_dict(), "known": True}}


def setup_start(payload: dict[str, Any]) -> dict[str, Any]:
    step = str(payload.get("step") or "model")
    model = str(payload.get("model") or "").strip() or DEFAULT_MODEL
    if not MODEL_NAME.fullmatch(model):
        raise BadRequest(f"Недопустимое имя модели: '{model}'")
    return {"setup": setup_mod.start(step, model)}


def setup_status(payload: dict[str, Any]) -> dict[str, Any]:
    return {"setup": setup_mod.status()}


def exports_dir() -> Path:
    override = os.environ.get("FORGE_EXPORTS_DIR")
    return Path(override) if override else paths.user_dir() / "exports"


EXPORT_NAME = re.compile(r'[^\\/:*?"<>|]+\.xlsx')


def card_sheets(payload: dict[str, Any]) -> list[cards_mod.Sheet]:
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("calc"), dict):
        raise BadRequest("Нет расчёта для карт")
    if not (data.get("route") or {}).get("operations"):
        raise BadRequest("Карты собираются только с маршрутом: сначала постройте маршрут")
    forms = loader.route_card_forms()
    return [cards_mod.route_card(data, forms), cards_mod.forging_card(data, forms)]


def cards_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {"sheets": [dataclasses.asdict(s) for s in card_sheets(payload)]}


def export_cards(payload: dict[str, Any]) -> dict[str, Any]:
    body = xlsx_mod.workbook(card_sheets(payload))
    data = payload["data"]
    part = data["calc"].get("part") or {}
    base = re.sub(r'[\\/:*?"<>|\s]+', "_", str(part.get("designation") or part.get("name") or "деталь")).strip("_")
    name = f"{base or 'деталь'}_карты_{time.strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
    folder = exports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(body)
    return {"file": name, "path": str(folder / name), "url": "/export/" + urllib.parse.quote(name)}


def update_check(payload: dict[str, Any]) -> dict[str, Any]:
    return {"update": update_mod.check(fresh=bool(payload.get("fresh")))}


def update_install(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = update_mod.install()
    except ValueError as exc:
        raise BadRequest(str(exc))
    except OSError as exc:
        raise BadRequest(f"Не удалось скачать обновление: {exc}")
    update_mod.quit_soon()
    return {"update": result}


ROUTES = {
    ("GET", "/api/update"): update_check,
    ("POST", "/api/update"): update_check,
    ("POST", "/api/update/install"): update_install,
    ("GET", "/api/state"): lambda payload: state(),
    ("GET", "/api/ping"): lambda payload: {"ok": True},
    ("GET", "/api/ollama"): lambda payload: {"ollama": ollama_state()},
    ("GET", "/api/forms"): lambda payload: {"forms": loader.route_card_forms()},
    ("GET", "/api/setup"): setup_status,
    ("POST", "/api/setup"): setup_start,
    ("POST", "/api/calc"): calc_only,
    ("POST", "/api/route"): route,
    ("POST", "/api/chat"): chat,
    ("GET", "/api/settings"): settings_get,
    ("POST", "/api/settings"): settings_save,
    ("GET", "/api/history"): history_list,
    ("POST", "/api/history/get"): history_get,
    ("POST", "/api/history/save"): history_save,
    ("POST", "/api/history/rename"): history_rename,
    ("POST", "/api/history/delete"): history_delete,
    ("GET", "/api/lessons"): lessons_list,
    ("POST", "/api/lessons/add"): lessons_add,
    ("POST", "/api/lessons/update"): lessons_update,
    ("POST", "/api/lessons/delete"): lessons_delete,
    ("POST", "/api/lessons/repeat"): lessons_repeat,
    ("POST", "/api/progress"): progress,
    ("POST", "/api/cancel"): cancel,
    ("POST", "/api/export"): export_cards,
    ("POST", "/api/cards"): cards_view,
}


def dispatch(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    handler = ROUTES.get((method, path))
    if handler is None:
        return 404, {"error": f"Нет обработчика для {method} {path}"}
    try:
        return 200, handler(payload or {})
    except BadRequest as exc:
        return 400, {"error": str(exc)}
    except loader.ReferenceError as exc:
        return 400, {"error": str(exc)}
    except workspace.WorkspaceError as exc:
        return 400, {"error": str(exc)}
    except lessons.LessonError as exc:
        return 400, {"error": str(exc)}
    except Exception as exc:
        return 500, {"error": f"{type(exc).__name__}: {exc}"}


_last_request = time.monotonic()


def last_request() -> float:
    return _last_request


def _touch() -> None:
    global _last_request
    _last_request = time.monotonic()


MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def request_refused(method: str, host: str, origin: str, content_type: str, port: int) -> str:
    allowed = {f"{h}:{port}" for h in LOCAL_HOSTS}
    if host not in allowed:
        return f"Чужой адрес сервера: {host or '—'}"
    if origin and origin != "null" and origin.split("://", 1)[-1] not in allowed:
        return f"Запрос с чужой страницы: {origin}"
    if method == "POST" and not content_type.lower().startswith("application/json"):
        return "Тело запроса должно быть application/json"
    return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "FORGE"
    protocol_version = "HTTP/1.1"

    def _refused(self, method: str) -> bool:
        why = request_refused(method, self.headers.get("Host") or "", self.headers.get("Origin") or "",
                              self.headers.get("Content-Type") or "", self.server.server_address[1])
        if why:
            self._send_json(403, {"error": why})
        return bool(why)

    def log_message(self, format: str, *args: Any) -> None:
        if str(args[1]).startswith(("4", "5")) if len(args) > 1 else False:
            super().log_message(format, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _serve_static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (HTML_DIR / name).resolve()
        if not target.is_relative_to(HTML_DIR.resolve()) or not target.is_file():
            self._send_json(404, {"error": f"Файл не найден: {name}"})
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _serve_export(self, name: str) -> None:
        folder = exports_dir().resolve()
        target = (folder / name).resolve()
        if not EXPORT_NAME.fullmatch(name) or not target.is_relative_to(folder) or not target.is_file():
            self._send_json(404, {"error": f"Файл не найден: {name}"})
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(name))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        _touch()
        if self._refused("GET"):
            return
        if self.path.startswith("/export/"):
            self._serve_export(urllib.parse.unquote(self.path[len("/export/"):]))
        elif self.path.startswith("/api/"):
            status, data = dispatch("GET", self.path)
            self._send_json(status, data)
        else:
            self._serve_static(self.path)

    def do_POST(self) -> None:
        _touch()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if self._refused("POST"):
            return
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"Тело запроса не JSON: {exc}"})
            return
        status, data = dispatch("POST", self.path, payload)
        self._send_json(status, data)


def prepare() -> None:
    problems = loader.integrity()
    if problems:
        print("Справочники повреждены: " + "; ".join(problems))
    try:
        lessons.add_starter()
    except OSError as exc:
        print(f"Уроки из комплекта не добавлены: {exc}")


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    prepare()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"FORGE: http://{host}:{port}")
    print("Остановить — Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        httpd.server_close()
