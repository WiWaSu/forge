from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .llm.client import DEFAULT_HOST

OLLAMA_SITE = "https://ollama.com/download"
WINGET_PACKAGE = "Ollama.Ollama"
MAX_LINES = 40
START_WAIT_S = 25

STEPS = ("ollama", "start", "model")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _no_window() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}


def ollama_path() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    env = os.environ
    for base, parts in ((env.get("LOCALAPPDATA"), ("Programs", "Ollama")),
                        (env.get("ProgramFiles"), ("Ollama",)),
                        (env.get("ProgramW6432"), ("Ollama",))):
        if base:
            candidate = Path(base).joinpath(*parts, "ollama.exe")
            if candidate.exists():
                return str(candidate)
    return None


def app_path() -> str | None:
    exe = ollama_path()
    app = Path(exe).with_name("ollama app.exe") if exe else None
    return str(app) if app and app.exists() else None


def ollama_installed() -> bool:
    return ollama_path() is not None


@dataclass
class TaskState:

    step: str = ""
    running: bool = False
    finished: bool = False
    ok: bool = False
    target: str = ""
    error: str = ""
    lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**asdict(self), "lines": self.lines[-MAX_LINES:]}


_state = TaskState()
_lock = threading.Lock()


def status() -> dict:
    with _lock:
        return _state.to_dict()


def _command(step: str, model: str) -> tuple[list[str], str]:
    if step == "ollama":
        return (
            ["winget", "install", "--id", WINGET_PACKAGE, "--exact",
             "--accept-package-agreements", "--accept-source-agreements"],
            WINGET_PACKAGE,
        )
    exe = ollama_path() or "ollama"
    if step == "start":
        app = app_path()
        return ([app] if app else [exe, "serve"]), "Ollama"
    return [exe, "pull", model], model


def _missing_tool(step: str) -> str:
    if step == "ollama":
        if not shutil.which("winget"):
            return (
                "В системе нет winget — это менеджер пакетов Windows, "
                f"он есть в Windows 11 и свежих Windows 10. Установите Ollama вручную: {OLLAMA_SITE}"
            )
        return ""
    if not ollama_installed():
        return (
            "Ollama не найдена. Сначала установите её — "
            "шагом выше или вручную с " + OLLAMA_SITE
        )
    return ""


def _service_answers() -> bool:
    try:
        with urllib.request.urlopen(f"{DEFAULT_HOST}/api/tags", timeout=2):
            return True
    except OSError:
        return False


def _start_service() -> None:
    command, _ = _command("start", "")
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **_no_window())
    except OSError as exc:
        with _lock:
            _state.running = False
            _state.finished = True
            _state.error = f"Не удалось запустить Ollama: {exc}"
        return

    for second in range(START_WAIT_S):
        time.sleep(1)
        with _lock:
            _state.lines = [f"Жду ответа сервиса… {second + 1} с"]
        if _service_answers():
            with _lock:
                _state.running = False
                _state.finished = True
                _state.ok = True
                _state.lines.append("Ollama отвечает.")
            return

    with _lock:
        _state.running = False
        _state.finished = True
        _state.error = (
            f"Ollama не ответила за {START_WAIT_S} с. Попробуйте запустить её из меню «Пуск» "
            "и обновить страницу."
        )


def _run(step: str, model: str) -> None:
    command, target = _command(step, model)
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_no_window(),
        )
    except FileNotFoundError:
        with _lock:
            _state.running = False
            _state.finished = True
            _state.error = f"Команда '{command[0]}' не найдена. {OLLAMA_SITE}"
        return
    except OSError as exc:
        with _lock:
            _state.running = False
            _state.finished = True
            _state.error = f"Не удалось запустить: {exc}"
        return

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        with _lock:
            _state.lines.append(line[-200:])
            del _state.lines[:-MAX_LINES]
    code = proc.wait()

    with _lock:
        _state.running = False
        _state.finished = True
        _state.ok = code == 0
        if code != 0:
            _state.error = (
                f"Шаг завершился с кодом {code}. "
                "Если появлялось окно с запросом прав и вы его закрыли — "
                "это нормальный отказ, попробуйте снова."
                if step == "ollama" else
                f"Загрузка завершилась с кодом {code}. Смотри вывод ниже."
            )


def start(step: str, model: str) -> dict:
    global _state
    if step not in STEPS:
        return {**status(), "error": f"Неизвестный шаг '{step}'"}

    with _lock:
        if _state.running:
            return _state.to_dict()

    problem = _missing_tool(step)
    if problem:
        with _lock:
            _state = TaskState(step=step, finished=True, error=problem)
            return _state.to_dict()

    _, target = _command(step, model)
    with _lock:
        _state = TaskState(step=step, running=True, target=target)
    worker = _start_service if step == "start" else (lambda: _run(step, model))
    threading.Thread(target=worker, daemon=True).start()
    return status()
