from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__, paths

REPO = "WiWaSu/forge"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
LATEST_PAGE = f"{RELEASES_PAGE}/latest"
ASSET = "FORGE.exe"
ASSET_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
CACHE_SECONDS = 3600
TIMEOUT = 10
DOWNLOAD_TIMEOUT = 300
NEW_EXE = "FORGE.new.exe"
SCRIPT = "forge-update.bat"

_cache: dict[str, Any] = {}
_lock = threading.Lock()


def version_tuple(text: str) -> tuple[int, int, int]:
    nums = [int(x) for x in re.findall(r"\d+", str(text))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def _fetch(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "FORGE"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _from_page(url: str = LATEST_PAGE) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "FORGE"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        final = response.geturl()
    found = re.search(r"/releases/tag/([^/?#]+)$", final)
    if not found:
        raise urllib.error.HTTPError(final, 404, "Not Found", {}, None)
    tag = urllib.parse.unquote(found.group(1))
    return {"tag_name": tag, "html_url": final,
            "assets": [{"name": ASSET, "browser_download_url": f"{RELEASES_PAGE}/download/{tag}/{ASSET}"}]}


def check(current: str = __version__, fetch: Callable[[str], dict[str, Any]] = _fetch,
          fresh: bool = False, fallback: Callable[[], dict[str, Any]] = _from_page) -> dict[str, Any]:
    with _lock:
        if not fresh and _cache.get("current") == current and time.monotonic() - _cache.get("at", 0) < CACHE_SECONDS:
            return dict(_cache["info"])
    info: dict[str, Any] = {"current": current, "latest": "", "newer": False, "notes": "", "page": RELEASES_PAGE,
                            "asset_url": "", "can_install": paths.frozen(), "error": ""}
    try:
        try:
            data = fetch(LATEST_URL)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return info
            data = fallback()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            info["error"] = f"Не удалось проверить обновления: {exc}"
        return info
    except Exception as exc:
        info["error"] = f"Не удалось проверить обновления: {exc}"
        return info
    tag = str(data.get("tag_name") or "")
    info.update(latest=tag.lstrip("vV"), notes=str(data.get("body") or ""), page=str(data.get("html_url") or RELEASES_PAGE))
    info["newer"] = bool(tag) and version_tuple(tag) > version_tuple(current)
    for asset in data.get("assets") or []:
        if asset.get("name") == ASSET:
            info["asset_url"] = str(asset.get("browser_download_url") or "")
    with _lock:
        _cache.update(current=current, at=time.monotonic(), info=dict(info))
    return info


def trusted(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname in ASSET_HOSTS


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "FORGE"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        if not trusted(response.geturl()):
            raise ValueError(f"Обновление пришло не с GitHub: {response.geturl()}")
        data = response.read()
    if not data.startswith(b"MZ"):
        raise ValueError("Скачанный файл — не программа Windows")
    target.write_bytes(data)


def script_text(exe: Path) -> str:
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        f'cd /d "{exe.parent}"',
        "set tries=0",
        ":retry",
        f'move /y "{NEW_EXE}" "{exe.name}" >nul 2>&1 && goto done',
        "set /a tries+=1",
        "if %tries% geq 60 goto fail",
        "timeout /t 1 /nobreak >nul",
        "goto retry",
        ":done",
        f'start "" "{exe.name}"',
        'del "%~f0"',
        "exit /b 0",
        ":fail",
        f"echo Не удалось заменить {exe.name}: закройте FORGE и запустите {SCRIPT} ещё раз.",
        "pause",
        "",
    ])


def install(info: Optional[dict[str, Any]] = None, exe: Optional[Path] = None,
            fetch_file: Callable[[str, Path], None] = download,
            launch: Callable[[Path], None] = lambda script: subprocess.Popen(
                ["cmd", "/c", str(script)], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                close_fds=True)) -> dict[str, Any]:
    if exe is None:
        if not paths.frozen():
            raise ValueError(f"Обновление ставится только в FORGE.exe. Код новой версии — на {RELEASES_PAGE}")
        exe = Path(sys.executable).resolve()
    info = info or check(fresh=True)
    if not info.get("newer"):
        raise ValueError("Новой версии нет")
    url = info.get("asset_url") or ""
    if not trusted(url):
        raise ValueError(f"В релизе {info.get('latest')} нет файла {ASSET} на GitHub")
    fetch_file(url, exe.parent / NEW_EXE)
    script = exe.parent / SCRIPT
    script.write_text(script_text(exe), encoding="utf-8")
    launch(script)
    return {"installing": info.get("latest"), "script": str(script)}


def quit_soon(delay: float = 1.5) -> None:
    threading.Timer(delay, lambda: os._exit(0)).start()
