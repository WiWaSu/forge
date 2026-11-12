from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import paths

THINK_LEVELS = (True, False, "low", "medium", "high")


@dataclass
class Profile:
    model: str
    title: str = ""
    temperature: Optional[float] = None
    num_ctx: Optional[int] = None
    think: Any = None
    options: dict[str, Any] = field(default_factory=dict)
    prompt_notes: list[str] = field(default_factory=list)
    known_issues: list[str] = field(default_factory=list)
    file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def find(model: str, dirs: Optional[list[Path]] = None) -> Profile:
    for folder in dirs if dirs is not None else paths.models_dirs():
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            data = _read(path)
            if not data or data.get("model") != model:
                continue
            return Profile(
                model=model,
                title=str(data.get("title") or ""),
                temperature=data.get("temperature"),
                num_ctx=data.get("num_ctx"),
                think=data.get("think") if data.get("think") in THINK_LEVELS else None,
                options=dict(data.get("options") or {}),
                prompt_notes=[str(s) for s in data.get("prompt_notes") or []],
                known_issues=[str(s) for s in data.get("known_issues") or []],
                file=str(path),
            )
    return Profile(model=model)


def think_for(model: str, think: Any) -> Any:
    if think is None:
        return None
    if "gpt-oss" in model.lower():
        return think if isinstance(think, str) else None
    return bool(think)


def client_kwargs(profile: Profile) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"options": dict(profile.options),
                              "prompt_notes": list(profile.prompt_notes)}
    if profile.temperature is not None:
        kwargs["temperature"] = float(profile.temperature)
    if profile.num_ctx:
        kwargs["num_ctx"] = int(profile.num_ctx)
    if profile.think is not None:
        kwargs["think"] = profile.think
    return kwargs
