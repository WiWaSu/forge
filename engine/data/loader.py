from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .. import paths

REFERENCE_DIR = paths.knowledge() / "reference"
GOST_DIR = paths.knowledge() / "gost"

REQUIRED_FILES = [
    "llm_catalog.json",
    "materials.json",
    "heat_treatment.json",
    "steel_groups.json",
    "complexity.json",
    "accuracy_classes.json",
    "kr_coefficients.json",
    "gost7505_tables.json",
    "gost7829_tables.json",
    "allowances_fallback.json",
    "losses.json",
    "stock_round.json",
    "equipment.json",
    "operations.json",
    "route_card_forms.json",
]


class ReferenceError(RuntimeError):
    pass


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    path = REFERENCE_DIR / name
    if not path.exists():
        raise ReferenceError(f"Справочник не найден: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def check_integrity() -> list[str]:
    problems: list[str] = []
    for name in REQUIRED_FILES:
        try:
            data = load(name)
        except (OSError, ValueError) as exc:
            problems.append(f"{name}: {exc}")
            continue
        meta = data.get("_meta", {})
        if not meta.get("source"):
            problems.append(f"{name}: не указан источник в _meta.source")
    return problems


@lru_cache(maxsize=1)
def integrity() -> tuple[str, ...]:
    return tuple(check_integrity())


def material(grade: str) -> dict[str, Any]:
    data = load("materials.json")
    for item in data["materials"]:
        if item["grade"].upper() == grade.upper():
            return item
    known = ", ".join(m["grade"] for m in data["materials"])
    raise ReferenceError(f"Марка стали '{grade}' отсутствует в справочнике. Доступны: {known}")


def heat_treatment(grade: str) -> dict[str, Any]:
    data = load("heat_treatment.json")
    for key, item in data["steels"].items():
        if key.upper() == grade.upper():
            return item
    return data["default"]


def density() -> float:
    return float(load("materials.json")["density_kg_m3"])


def steel_groups() -> list[dict[str, Any]]:
    return load("steel_groups.json")["groups"]


def complexity_levels() -> list[dict[str, Any]]:
    return load("complexity.json")["levels"]


def accuracy_by_equipment(equipment_type: str) -> dict[str, Any]:
    for item in load("accuracy_classes.json")["by_equipment"]:
        if item["equipment"] == equipment_type:
            return item
    raise ReferenceError(f"Нет данных о классах точности для оборудования '{equipment_type}'")


def kr_group(code: str) -> dict[str, Any]:
    for item in load("kr_coefficients.json")["groups"]:
        if item["code"] == code:
            return item
    raise ReferenceError(f"Нет группы Кр с кодом '{code}'")


def kr_part_types() -> list[dict[str, Any]]:
    return load("kr_coefficients.json")["part_types"]


def kr_group_for_part_type(part_type: str) -> dict[str, Any]:
    for item in kr_part_types():
        if item["code"] == part_type:
            return kr_group(item["group"])
    raise ReferenceError(f"Нет группы Кр для типа детали '{part_type}'")


def gost7505() -> dict[str, Any]:
    return load("gost7505_tables.json")


def gost7505_filled() -> bool:
    return gost7505().get("_meta", {}).get("filled") in (True, "partial")


def gost7505_verified() -> bool:
    return bool(gost7505().get("_meta", {}).get("verified"))


def gost7505_indices() -> list[int]:
    return list(gost7505()["main_allowances"].get("verified_indices", []))


def fallback_allowances() -> dict[str, Any]:
    return load("allowances_fallback.json")


def losses() -> dict[str, Any]:
    return load("losses.json")


def heating_hold_min_per_mm(heater: str) -> float:
    block = load("losses.json").get("heating_hold", {})
    return float(block.get("min_per_mm", {}).get(heater, 0.0))


def scale_percent(key: str) -> float:
    for item in losses()["scale_loss_percent"]:
        if item["heating"] == key:
            return float(item["percent"])
    raise ReferenceError(f"Нет данных об угаре для режима нагрева '{key}'")


def flash_percent(complexity_code: str) -> float:
    for item in losses()["flash_percent_by_complexity"]:
        if item["complexity"] == complexity_code:
            return float(item["percent"])
    raise ReferenceError(f"Нет данных о доле облоя для степени сложности '{complexity_code}'")


def stock_diameters() -> list[float]:
    return [float(d) for d in load("stock_round.json")["diameters_mm"]]


def equipment(equipment_type: str) -> dict[str, Any]:
    for item in load("equipment.json")["equipment"]:
        if item["type"] == equipment_type:
            return item
    raise ReferenceError(f"Оборудование '{equipment_type}' отсутствует в справочнике")


def operations() -> list[dict[str, Any]]:
    return load("operations.json")["operations"]


def operation_codes() -> set[str]:
    return {op["code"] for op in operations()}


def processes() -> list[dict[str, Any]]:
    return load("operations.json")["processes"]


def route_card_forms() -> dict[str, Any]:
    return load("route_card_forms.json")


def source_of(name: str) -> str:
    return load(name).get("_meta", {}).get("source", name)


def is_verified(name: str) -> bool:
    return bool(load(name).get("_meta", {}).get("verified"))
