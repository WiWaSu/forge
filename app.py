from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import __version__, paths
from engine.api import server

HANDOFF_SECONDS = 5
SILENCE_SECONDS = 180
LOG_LIMIT = 200_000

BROWSER_EXES = ["msedge.exe", "chrome.exe"]
BROWSER_DIRS = [("Microsoft", "Edge", "Application"), ("Google", "Chrome", "Application")]
BROWSERS_OTHER = ["microsoft-edge", "google-chrome", "chromium", "chromium-browser"]


def _registry_app_path(exe: str) -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as k:
                value, _ = winreg.QueryValueEx(k, None)
        except OSError:
            continue
        if value and Path(value).exists():
            return value
    return None


def browser_candidates() -> list[str]:
    env = os.environ
    bases = [env.get("ProgramFiles(x86)"), env.get("ProgramFiles"),
             env.get("ProgramW6432"), env.get("LOCALAPPDATA")]
    found = []
    for parts, exe in zip(BROWSER_DIRS, BROWSER_EXES):
        for base in bases:
            if base:
                found.append(str(Path(base).joinpath(*parts, exe)))
    return found


def find_browser() -> str | None:
    if os.name == "nt":
        for exe in BROWSER_EXES:
            path = _registry_app_path(exe)
            if path:
                return path
        for path in browser_candidates():
            if Path(path).exists():
                return path
    for name in BROWSERS_OTHER + BROWSER_EXES:
        found = shutil.which(name)
        if found:
            return found
    return None


def free_port(preferred: int = 8765) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def window_profile() -> Path:
    return Path(tempfile.gettempdir()) / f"forge-window-{os.getpid()}"


def open_window(browser: str, url: str, profile: Path) -> subprocess.Popen:
    return subprocess.Popen([
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
    ])


def log(text: str) -> None:
    path = paths.user_dir() / "forge.log"
    try:
        mode = "w" if path.exists() and path.stat().st_size > LOG_LIMIT else "a"
        with path.open(mode, encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
    except OSError:
        pass


def wait_for_window(proc: subprocess.Popen) -> None:
    started = time.monotonic()
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        return
    lived = time.monotonic() - started
    log(f"процесс браузера завершился через {lived:.1f} с, код {code}")
    if lived > HANDOFF_SECONDS:
        return
    print("Браузер передал окно другому своему процессу. Программа выключится,")
    print("когда окно закроют (через пару минут после этого).")
    log("окно у другого процесса браузера — слежу за страницей")
    wait_for_silence()


def wait_for_silence() -> None:
    try:
        while time.monotonic() - server.last_request() < SILENCE_SECONDS:
            time.sleep(2)
    except KeyboardInterrupt:
        return
    log(f"страница молчит {SILENCE_SECONDS} с — окно закрыто")


def safe_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=f"FORGE {__version__}")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 — подобрать свободный")
    ap.add_argument("--browser-tab", action="store_true",
                    help="открыть обычную вкладку вместо окна приложения")
    args = ap.parse_args()
    safe_console()

    port = args.port or free_port()
    url = f"http://{args.host}:{port}"
    log(f"запуск FORGE {__version__}, {url}")

    server.prepare()
    try:
        httpd = server.ThreadingHTTPServer((args.host, port), server.Handler)
    except OSError as exc:
        log(f"сервер не поднялся на {url}: {exc}")
        raise
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"FORGE {__version__}")
    print(f"Интерфейс: {url}")

    browser = None if args.browser_tab else find_browser()
    profile = window_profile()
    if browser:
        time.sleep(0.4)
        log(f"окно: {browser}")
        proc = open_window(browser, url, profile)
        print("Окно открыто. Закройте его, чтобы выйти.")
        wait_for_window(proc)
    else:
        log("окно: обычная вкладка браузера по умолчанию")
        if not args.browser_tab:
            print("Браузер на Chromium не найден — открываю обычную вкладку.")
        webbrowser.open(url)
        print("Остановить — Ctrl+C в этом окне.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    httpd.shutdown()
    httpd.server_close()
    time.sleep(0.5)
    shutil.rmtree(profile, ignore_errors=True)
    log("остановлено")
    print("Остановлено.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        log("ошибка:\n" + traceback.format_exc())
        raise
