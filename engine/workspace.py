from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import paths

LAYOUTS = ("A", "B", "C")
LAYOUTS_READY = ("A", "B", "C")
THEMES = ("system", "light", "dark")

DEFAULTS: dict[str, Any] = {
    "layout": "A",
    "theme": "system",
    "model": "",
    "use_llm": True,
    "mode": "stepwise",
    "transitions": True,
    "show_requests": True,
    "check_updates": True,
    "accent": "",
    "think": {},
}

THINK_LEVELS = ("low", "medium", "high")
ACCENT_RE = re.compile(r"#[0-9a-fA-F]{6}")

TITLE_MAX = 80
ID_RE = re.compile(r"^[0-9a-f]{32}$")

_lock = threading.Lock()


class WorkspaceError(ValueError):
    pass


def settings_file() -> Path:
    override = os.environ.get("FORGE_SETTINGS_FILE")
    return Path(override) if override else paths.user_dir() / "settings.json"


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def clean_settings(raw: dict[str, Any]) -> dict[str, Any]:
    out = {**DEFAULTS, "think": {}}
    if not isinstance(raw, dict):
        return out
    if raw.get("layout") in LAYOUTS:
        out["layout"] = raw["layout"]
    if raw.get("theme") in THEMES:
        out["theme"] = raw["theme"]
    if isinstance(raw.get("model"), str):
        out["model"] = raw["model"].strip()[:200]
    if raw.get("mode") in ("baseline", "single", "stepwise"):
        out["mode"] = raw["mode"]
    for key in ("use_llm", "transitions", "show_requests", "check_updates"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    if isinstance(raw.get("accent"), str) and ACCENT_RE.fullmatch(raw["accent"]):
        out["accent"] = raw["accent"].lower()
    if isinstance(raw.get("think"), dict):
        out["think"] = {k.strip()[:200]: v for k, v in raw["think"].items()
                        if isinstance(k, str) and k.strip() and (isinstance(v, bool) or v in THINK_LEVELS)}
    return out


def load_settings() -> dict[str, Any]:
    data = _read_json(settings_file())
    return {
        "first_run": data is None,
        "settings": clean_settings(data or {}),
        "layouts_ready": list(LAYOUTS_READY),
    }


def save_settings(raw: dict[str, Any]) -> dict[str, Any]:
    settings = clean_settings(raw)
    with _lock:
        _write_json(settings_file(), settings)
    return {"first_run": False, "settings": settings, "layouts_ready": list(LAYOUTS_READY)}


def history_dir() -> Path:
    override = os.environ.get("FORGE_HISTORY_DIR")
    return Path(override) if override else paths.user_dir() / "history"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _path(chat_id: str) -> Path:
    if not isinstance(chat_id, str) or not ID_RE.match(chat_id):
        raise WorkspaceError("Неверный номер чата")
    return history_dir() / f"{chat_id}.json"


def _summary(chat: dict[str, Any]) -> dict[str, Any]:
    part = chat.get("part") or {}
    return {
        "id": chat.get("id"),
        "title": chat.get("title") or "Новый чат",
        "created": chat.get("created"),
        "updated": chat.get("updated"),
        "model": chat.get("model") or "",
        "designation": part.get("designation") or "",
        "part_name": part.get("name") or "",
        "has_result": bool(chat.get("result")),
    }


def list_chats() -> list[dict[str, Any]]:
    folder = history_dir()
    if not folder.is_dir():
        return []
    items = []
    for path in folder.glob("*.json"):
        data = _read_json(path)
        if data and ID_RE.match(str(data.get("id") or "")):
            items.append(_summary(data))
    return sorted(items, key=lambda c: c.get("updated") or "", reverse=True)


def get_chat(chat_id: str) -> dict[str, Any]:
    data = _read_json(_path(chat_id))
    if data is None:
        raise WorkspaceError("Такого чата нет")
    return data


def save_chat(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WorkspaceError("Чат должен быть объектом")
    chat_id = raw.get("id") or uuid.uuid4().hex
    path = _path(chat_id)
    with _lock:
        old = _read_json(path) or {}
        chat = {
            "id": chat_id,
            "title": str(raw.get("title") or old.get("title") or "Новый чат")[:TITLE_MAX],
            "created": old.get("created") or _now(),
            "updated": _now(),
            "model": str(raw.get("model") if raw.get("model") is not None else old.get("model") or ""),
            "part": raw.get("part") if isinstance(raw.get("part"), dict) else old.get("part"),
            "result": raw.get("result") if isinstance(raw.get("result"), dict) else old.get("result"),
            "events": raw.get("events") if isinstance(raw.get("events"), list) else old.get("events", []),
            "messages": raw.get("messages") if isinstance(raw.get("messages"), list)
            else old.get("messages", []),
        }
        _write_json(path, chat)
    return _summary(chat)


def rename_chat(chat_id: str, title: str) -> dict[str, Any]:
    chat = get_chat(chat_id)
    title = str(title or "").strip()
    if not title:
        raise WorkspaceError("Пустое название")
    chat["title"] = title[:TITLE_MAX]
    with _lock:
        _write_json(_path(chat_id), chat)
    return _summary(chat)


def delete_chat(chat_id: str) -> bool:
    path = _path(chat_id)
    with _lock:
        if not path.exists():
            return False
        path.unlink()
    return True
