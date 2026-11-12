from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import paths

STATUSES = ("candidate", "accepted", "rejected")
STATUS_NAMES = {"candidate": "на проверке", "accepted": "принят", "rejected": "отклонён"}

TEXT_MAX = 600
RULE_MAX = 400
MAX_IN_REQUEST = 8
ID_RE = re.compile(r"^[0-9a-f]{32}$")
CODE_RE = re.compile(r"^\d{3}_[a-z_]+$")

HEADER = ("УРОКИ ИЗ ЖУРНАЛА ОШИБОК. Эти ошибки уже делались и исправлены инженером-технологом. "
          "Не повторяй их; если урок противоречит расчёту, верен расчёт.")

_lock = threading.Lock()


class LessonError(ValueError):
    pass


def lessons_file() -> Path:
    override = os.environ.get("FORGE_LESSONS_FILE")
    return Path(override) if override else paths.user_dir() / "lessons.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read() -> dict[str, Any]:
    try:
        data = json.loads(lessons_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load() -> list[dict[str, Any]]:
    items = _read().get("lessons")
    return [x for x in items or [] if isinstance(x, dict) and ID_RE.match(str(x.get("id") or ""))]


def _save(items: list[dict[str, Any]], starter: Optional[list[str]] = None) -> None:
    if starter is None:
        starter = [str(x) for x in _read().get("starter") or []]
    data: dict[str, Any] = {"lessons": items}
    if starter:
        data["starter"] = sorted(set(starter))
    path = lessons_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _text(raw: Any, limit: int) -> str:
    return re.sub(r"[ \t]+", " ", str(raw or "")).strip()[:limit]


def _find(items: list[dict[str, Any]], lesson_id: str) -> dict[str, Any]:
    for item in items:
        if item.get("id") == lesson_id:
            return item
    raise LessonError("Такой записи в журнале нет")


def default_rule(item: dict[str, Any]) -> str:
    return _text(item.get("right") or "", RULE_MAX)


def add(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LessonError("Запись должна быть объектом")
    right = _text(raw.get("right"), TEXT_MAX)
    if len(right) < 5:
        raise LessonError("Напишите, как правильно: без этого урока не получится")
    op_code = str(raw.get("op_code") or "").strip()
    if op_code and not CODE_RE.match(op_code):
        raise LessonError("Неверный код операции")
    item = {
        "id": uuid.uuid4().hex,
        "status": "candidate",
        "created": _now(),
        "updated": _now(),
        "where": "chat" if raw.get("where") == "chat" else "route",
        "part_type": _text(raw.get("part_type"), 40),
        "part_type_name": _text(raw.get("part_type_name"), 80),
        "op_code": op_code,
        "op_name": _text(raw.get("op_name"), 120),
        "wrong": _text(raw.get("wrong"), TEXT_MAX),
        "right": right,
        "rule": _text(raw.get("rule"), RULE_MAX),
        "model": _text(raw.get("model"), 200),
        "designation": _text(raw.get("designation"), 80),
        "chat_id": str(raw.get("chat_id") or "") if ID_RE.match(str(raw.get("chat_id") or "")) else "",
        "used": 0,
        "last_used": "",
        "repeats": [],
    }
    if not item["rule"]:
        item["rule"] = default_rule(item)
    with _lock:
        items = _load()
        items.append(item)
        _save(items)
    return item


def update(lesson_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LessonError("Изменения должны быть объектом")
    with _lock:
        items = _load()
        item = _find(items, lesson_id)
        if "status" in raw:
            if raw["status"] not in STATUSES:
                raise LessonError("Неизвестное состояние записи")
            item["status"] = raw["status"]
        for key, limit in (("wrong", TEXT_MAX), ("right", TEXT_MAX)):
            if key in raw:
                item[key] = _text(raw[key], limit)
        if "rule" in raw:
            item["rule"] = _text(raw["rule"], RULE_MAX) or default_rule(item)
        if "part_type" in raw:
            item["part_type"] = _text(raw["part_type"], 40)
            if not item["part_type"]:
                item["part_type_name"] = ""
        if item["status"] == "accepted" and len(item.get("rule") or "") < 5:
            raise LessonError("Пустой урок принять нельзя: напишите, как правильно")
        item["updated"] = _now()
        _save(items)
    return item


def delete(lesson_id: str) -> bool:
    with _lock:
        items = _load()
        left = [x for x in items if x.get("id") != lesson_id]
        if len(left) == len(items):
            return False
        _save(left)
    return True


def repeat(lesson_id: str, model: str) -> dict[str, Any]:
    with _lock:
        items = _load()
        item = _find(items, lesson_id)
        item.setdefault("repeats", []).append({"model": _text(model, 200), "at": _now(),
                                               "after_accept": item.get("status") == "accepted"})
        item["updated"] = _now()
        _save(items)
    return item


def stats(items: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    items = _load() if items is None else items
    by: dict[str, dict[str, Any]] = {}

    def row(model: str) -> dict[str, Any]:
        return by.setdefault(model or "—", {"model": model or "—", "marked": 0, "accepted": 0,
                                            "repeats_after": 0})

    for item in items:
        if not item.get("starter"):
            r = row(item.get("model") or "")
            r["marked"] += 1
            if item.get("status") == "accepted":
                r["accepted"] += 1
        for rep in item.get("repeats") or []:
            if rep.get("after_accept"):
                row(rep.get("model") or "")["repeats_after"] += 1
    return sorted(by.values(), key=lambda r: (-r["marked"], r["model"]))


def listing() -> dict[str, Any]:
    items = _load()
    out: list[dict[str, Any]] = []
    for status in STATUSES:
        group = [x for x in items if x.get("status") == status]
        out.extend(sorted(group, key=lambda x: x.get("updated") or "", reverse=True))
    return {
        "lessons": out,
        "counts": {s: sum(1 for x in items if x.get("status") == s) for s in STATUSES},
        "stats": stats(items),
    }


def add_starter() -> int:
    from .data import loader

    try:
        kit = loader.load("lessons_starter.json").get("lessons") or []
    except (OSError, ValueError, loader.ReferenceError):
        return 0
    with _lock:
        data = _read()
        done = {str(x) for x in data.get("starter") or []}
        items = _load()
        added = 0
        for raw in kit:
            key = str(raw.get("key") or "")
            if not key or key in done:
                continue
            item = {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"forge-lesson:{key}").hex,
                "status": "accepted",
                "created": _now(),
                "updated": _now(),
                "where": "route",
                "part_type": _text(raw.get("part_type"), 40),
                "part_type_name": _text(raw.get("part_type_name"), 80),
                "op_code": str(raw.get("op_code") or ""),
                "op_name": _text(raw.get("op_name"), 120),
                "wrong": _text(raw.get("wrong"), TEXT_MAX),
                "right": _text(raw.get("right"), TEXT_MAX),
                "rule": _text(raw.get("rule"), RULE_MAX),
                "model": "",
                "designation": "",
                "chat_id": "",
                "used": 0,
                "last_used": "",
                "repeats": [],
                "starter": key,
            }
            if not item["rule"]:
                item["rule"] = default_rule(item)
            if not any(x.get("id") == item["id"] for x in items):
                items.append(item)
                added += 1
            done.add(key)
        if added or done != {str(x) for x in data.get("starter") or []}:
            _save(items, sorted(done))
    return added


def accepted_for(part_type: str) -> list[dict[str, Any]]:
    return [x for x in _load() if x.get("status") == "accepted"
            and (not x.get("part_type") or x.get("part_type") == part_type)]


def select(pool: Iterable[dict[str, Any]], ops: Optional[set[str]]) -> list[dict[str, Any]]:
    chosen = []
    for item in pool:
        code = item.get("op_code") or ""
        if code and ops is not None and code not in ops:
            continue
        chosen.append(item)
    specific = sorted([x for x in chosen if x.get("op_code")], key=lambda x: x.get("updated") or "", reverse=True)
    general = sorted([x for x in chosen if not x.get("op_code")], key=lambda x: x.get("updated") or "", reverse=True)
    return (specific + general)[:MAX_IN_REQUEST]


def render(chosen: list[dict[str, Any]]) -> str:
    lines = [HEADER]
    for item in chosen:
        where = f"[операция «{item.get('op_name') or item['op_code']}», код {item['op_code']}] " \
            if item.get("op_code") else ""
        lines.append(f"- {where}{item.get('rule') or default_rule(item)}")
    return "\n".join(lines)


def mark_used(ids: Iterable[str]) -> None:
    ids = set(ids)
    if not ids:
        return
    with _lock:
        items = _load()
        for item in items:
            if item.get("id") in ids:
                item["used"] = int(item.get("used") or 0) + 1
                item["last_used"] = _now()
        _save(items)


def _ops_of(messages: list[dict[str, str]]) -> Optional[set[str]]:
    users = [m.get("content") or "" for m in messages if m.get("role") == "user"]
    first = users[0] if users else ""
    if "ОПЕРАЦИЯ, которую нужно раскрыть" in first:
        found = re.search(r"Код:\s*(\d{3}_[a-z_]+)", first)
        return {found.group(1)} if found else None
    if "ВОПРОС:" in first:
        from .llm.stepwise import DECISIONS
        line = first.split("ВОПРОС:", 1)[1].strip().splitlines()[0]
        codes = {d["code"] for d in DECISIONS if d["question"] in line}
        return codes or set()
    return None


def _append_system(messages: list[dict[str, str]], text: str) -> list[dict[str, str]]:
    out, done = [], False
    for m in messages:
        if not done and m.get("role") == "system":
            m = {**m, "content": f"{m.get('content', '')}\n\n{text}"}
            done = True
        out.append(m)
    if not done:
        out.insert(0, {"role": "system", "content": text})
    return out


class LessonsClient:

    def __init__(self, inner: Any, part_type: str, pool: Optional[list[dict[str, Any]]] = None):
        self.inner = inner
        self.part_type = part_type
        self.pool = accepted_for(part_type) if pool is None else pool
        self.used: dict[str, dict[str, Any]] = {}

    def chat(self, messages: list[dict[str, str]]) -> str:
        chosen = select(self.pool, _ops_of(messages)) if self.pool else []
        if chosen:
            for item in chosen:
                self.used[item["id"]] = item
            messages = _append_system(messages, render(chosen))
        return self.inner.chat(messages)

    def summary(self) -> list[dict[str, str]]:
        return [{"id": x["id"], "op_code": x.get("op_code") or "", "rule": x.get("rule") or ""}
                for x in self.used.values()]

    def finish(self) -> None:
        try:
            mark_used(self.used.keys())
        except OSError:
            pass
