from __future__ import annotations

from .data import loader

PROCESS_NAMES = {
    "die_forging_hammer": "горячая объёмная штамповка на молоте",
    "die_forging_press": "горячая объёмная штамповка на КГШП",
    "die_forging_hcm": "горячая объёмная штамповка на ГКМ",
    "free_forging": "свободная ковка",
}

_GOST_LETTERS = {"M": "М", "C": "С", "T": "Т"}


def process_name(code: str) -> str:
    return PROCESS_NAMES.get(code, code)


def part_type_name(code: str) -> str:
    for item in loader.kr_part_types():
        if item["code"] == code:
            return item.get("short") or item["name"].split(",")[0].lower()
    return code


def equipment_name(code: str) -> str:
    for item in loader.load("equipment.json")["equipment"]:
        if item.get("type") == code:
            return item.get("name_ru", code)
    return code


def gost_code(value: str) -> str:
    if value and value[0] in _GOST_LETTERS:
        return _GOST_LETTERS[value[0]] + value[1:]
    return value
