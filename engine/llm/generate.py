from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import CalcResult
from ..progress import Report
from . import baseline, prompt, validate
from .client import LLMCancelled, LLMError

MAX_ATTEMPTS = 3


@dataclass
class RouteResult:

    route: dict[str, Any]
    source: str = "llm"
    attempts: int = 0
    errors: list[list[str]] = field(default_factory=list)
    raw_responses: list[str] = field(default_factory=list)
    fallback_reason: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "source": self.source,
            "attempts": self.attempts,
            "errors": self.errors,
            "fallback_reason": self.fallback_reason,
            "decisions": self.decisions,
        }


def parse_response(text: str) -> tuple[Optional[dict], Optional[str]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None, "В ответе нет JSON-объекта. Верни только JSON, без пояснений"
    try:
        return json.loads(cleaned[start:end + 1]), None
    except json.JSONDecodeError as exc:
        return None, f"JSON не разбирается: {exc}. Верни корректный JSON без текста вокруг"


def generate_route(
    calc: CalcResult,
    client: Any = None,
    max_attempts: int = MAX_ATTEMPTS,
    use_examples: bool = True,
    report: Optional[Report] = None,
) -> RouteResult:
    if client is None:
        route = baseline.build(calc)
        return RouteResult(route=route, source="baseline",
                           fallback_reason="Клиент модели не задан")

    messages = prompt.build_route_messages(calc, use_examples=use_examples)
    result = RouteResult(route={})

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        if report:
            report("маршрут целиком" if attempt == 1 else "маршрут целиком, переспрос",
                   attempt, max_attempts)
        try:
            text = client.chat(messages)
        except LLMCancelled as exc:
            result.route = baseline.build(calc)
            result.source = "baseline"
            result.fallback_reason = f"{exc} до ответа модели — маршрут построен по правилам"
            return result
        except LLMError as exc:
            result.route = baseline.build(calc)
            result.source = "baseline"
            result.fallback_reason = str(exc)
            return result

        result.raw_responses.append(text)
        data, parse_error = parse_response(text)

        errors = [parse_error] if parse_error else validate.validate(data, calc)
        if not errors:
            result.route = validate.normalize(data)
            result.source = "llm"
            return result

        result.errors.append(errors)
        if attempt < max_attempts:
            messages = prompt.build_route_messages(calc, use_examples=False) + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": validate.format_errors(errors)},
            ]

    result.route = baseline.build(calc)
    result.source = "baseline"
    result.fallback_reason = (
        f"Модель не дала корректный маршрут за {max_attempts} попытки. "
        f"Последние ошибки: {'; '.join(result.errors[-1])}"
    )
    return result


def compare(route_llm: dict[str, Any], route_baseline: dict[str, Any]) -> dict[str, Any]:
    codes_llm = [op["code"] for op in route_llm.get("operations", [])]
    codes_base = [op["code"] for op in route_baseline.get("operations", [])]
    set_llm, set_base = set(codes_llm), set(codes_base)

    common = set_llm & set_base
    union = set_llm | set_base
    return {
        "operations_llm": len(codes_llm),
        "operations_baseline": len(codes_base),
        "identical": codes_llm == codes_base,
        "extra_in_llm": sorted(set_llm - set_base),
        "missing_in_llm": sorted(set_base - set_llm),
        "same_order": [c for c in codes_llm if c in common] == [c for c in codes_base if c in common],
        "match_percent": round(len(common) / len(union) * 100, 1) if union else 0.0,
    }
