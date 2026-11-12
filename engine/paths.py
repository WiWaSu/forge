from __future__ import annotations

import sys
from pathlib import Path


def data_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[1]


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def knowledge() -> Path:
    return data_root() / "knowledge"


def instructions() -> Path:
    return data_root() / "instructions"


def html() -> Path:
    return data_root() / "html"


def user_dir() -> Path:
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def models_dirs() -> list[Path]:
    dirs = [user_dir() / "models"]
    bundled = data_root() / "models"
    if bundled not in dirs:
        dirs.append(bundled)
    return dirs
